"""
Visual grounding using Gemini bounding box detection + verification.

Pipeline per call:
  1. Send full-resolution screenshot → Gemini → bounding box
  2. Crop detected region → Gemini → YES/NO verification
  3. Return centre pixel coordinates on confirmation, else retry.
"""

import json
import os
import re
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image, ImageDraw

MODEL = "gemini-3-flash-preview"

# Output folder for detection result images (project_root/grounding/)
_GROUNDING_DIR = Path(__file__).parent.parent / "grounding"

# Cached API client — created once, reused across all ground_icon() calls
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _client

DETECTION_PROMPT = """\
Find the {target_description} on this Windows desktop screenshot.
Return its bounding box.
{exclusions}
Return ONLY a JSON array:
[{{"box_2d": [y_min, x_min, y_max, x_max], "label": "icon name"}}]

Rules:
- box_2d values are integers 0-1000
- y comes before x
- Return [] if not found
- Return ONLY JSON, no markdown\
"""

VERIFY_PROMPT = """\
Does this image show the {target_description}?
Answer YES or NO only.\
"""


def _strip_markdown(text: str) -> str:
    text = re.sub(r"```[a-zA-Z]*\s*", "", text)
    return text.replace("```", "").strip()


def _to_bytes(img: Image.Image, quality: int = 85) -> bytes:
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _img_part(img: Image.Image, quality: int = 85) -> types.Part:
    return types.Part(
        inline_data=types.Blob(mime_type="image/jpeg", data=_to_bytes(img, quality))
    )


def _call(client: genai.Client, contents: list, temperature: float = 0.0) -> str:
    response = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(temperature=temperature),
    )
    return response.text


def _detect(client, screenshot: Image.Image, target: str, exclusions: str = "") -> list[dict]:
    """Ask Gemini to return bounding boxes for the target icon."""
    prompt = DETECTION_PROMPT.format(target_description=target, exclusions=exclusions)
    # Use temperature=0.7 so that exclusion prompts can actually shift the output
    raw = _call(client, [_img_part(screenshot, quality=90), types.Part(text=prompt)], temperature=0.7)
    print(f"  [gemini] detect response: {raw.strip()}")
    return json.loads(_strip_markdown(raw))


def _verify(client, crop: Image.Image, target: str) -> bool:
    """Confirm the detected crop actually matches the target visually."""
    prompt = VERIFY_PROMPT.format(target_description=target)
    raw = _call(client, [_img_part(crop, quality=95), types.Part(text=prompt)])
    answer = raw.strip().upper()
    print(f"  [gemini] verify response: {answer}")
    return "YES" in answer


def ground_icon(
    target_description: str,
    screenshot: Image.Image,
    max_retries: int = 3,
    save_debug: bool = True,
) -> tuple[int, int] | None:
    """
    Locate a desktop icon using Gemini bounding box detection + verification.

    Args:
        target_description: Visual description of the icon to find.
        screenshot:         PIL Image of the current desktop.
        max_retries:        Attempts before giving up.
        save_debug:         Save annotated result image to grounding/ folder.

    Returns:
        (x, y) screen pixel coordinates of the icon centre, or None.
    """
    client = _get_client()
    W, H = screenshot.size

    # Working copy of the screenshot — wrong regions get masked out per retry
    search_img = screenshot.copy()
    excluded_boxes: list[tuple] = []  # failed normalized (y_min, x_min, y_max, x_max)

    for attempt in range(1, max_retries + 1):
        print(f"  [grounding] attempt {attempt}/{max_retries}")

        # Build exclusion text from previously failed regions
        if excluded_boxes:
            excl_lines = "\n".join(f"  - {b}" for b in excluded_boxes)
            exclusions = (
                f"IMPORTANT: These regions were already checked and are WRONG — "
                f"do NOT return boxes in these areas:\n{excl_lines}\n"
                f"The target must be somewhere else on the screen.\n"
            )
        else:
            exclusions = ""

        try:
            # ── Step 1: detect ───────────────────────────────────────────────
            boxes = _detect(client, search_img, target_description, exclusions)

            if not boxes:
                print("  [gemini] no boxes returned")
                time.sleep(2)
                continue

            box = boxes[0]
            y_min, x_min, y_max, x_max = box["box_2d"]

            # Skip immediately if model returned a box we already excluded
            if (y_min, x_min, y_max, x_max) in excluded_boxes:
                print("  [gemini] returned a previously excluded box — skipping verify")
                time.sleep(2)
                continue

            # Convert 0-1000 → screen pixels (always relative to original size)
            px_x1 = int((x_min / 1000) * W)
            px_y1 = int((y_min / 1000) * H)
            px_x2 = int((x_max / 1000) * W)
            px_y2 = int((y_max / 1000) * H)

            print(f"  [gemini] box=({px_x1},{px_y1})→({px_x2},{px_y2}) "
                  f"label='{box.get('label', '')}'")

            # Guard against degenerate boxes
            if px_x2 <= px_x1 or px_y2 <= px_y1:
                print("  [gemini] degenerate box, retrying")
                time.sleep(2)
                continue

            # ── Step 2: verify crop (from original, unmasked screenshot) ─────
            pad = 10
            crop = screenshot.crop((
                max(0, px_x1 - pad), max(0, px_y1 - pad),
                min(W, px_x2 + pad), min(H, px_y2 + pad),
            ))
            if not _verify(client, crop, target_description):
                print("  [gemini] verification failed — masking this region out")
                # Track the failed region in normalized 0-1000 coords for prompt exclusion
                excluded_boxes.append((y_min, x_min, y_max, x_max))
                # Grey out the wrong region so the next attempt looks elsewhere
                ImageDraw.Draw(search_img).rectangle(
                    [max(0, px_x1 - pad), max(0, px_y1 - pad),
                     min(W, px_x2 + pad), min(H, px_y2 + pad)],
                    fill=(100, 100, 100)
                )
                time.sleep(2)
                continue

            cx = (px_x1 + px_x2) // 2
            cy = (px_y1 + px_y2) // 2
            print(f"  [grounding] confirmed at ({cx}, {cy})")

            # Save annotated result image (skipped for popup/transient grounding)
            if save_debug:
                _GROUNDING_DIR.mkdir(exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                result_img = screenshot.copy()
                d = ImageDraw.Draw(result_img)
                d.rectangle([px_x1, px_y1, px_x2, px_y2], outline=(0, 255, 0), width=3)
                d.line([(cx - 20, cy), (cx + 20, cy)], fill=(255, 0, 0), width=3)
                d.line([(cx, cy - 20), (cx, cy + 20)], fill=(255, 0, 0), width=3)
                out_path = _GROUNDING_DIR / f"detected_{timestamp}.png"
                result_img.save(out_path)
                print(f"  [grounding] saved → {out_path}")

            return (cx, cy)

        except (json.JSONDecodeError, KeyError, IndexError) as exc:
            print(f"  [gemini] parse error: {exc}")
            time.sleep(2)
        except Exception as exc:
            msg = str(exc)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                wait = 30 * attempt
                print(f"  [gemini] rate limited – waiting {wait}s")
                time.sleep(wait)
            else:
                print(f"  [gemini] API error: {exc}")
                time.sleep(2)

    print("  [grounding] all attempts exhausted – returning None")
    return None
