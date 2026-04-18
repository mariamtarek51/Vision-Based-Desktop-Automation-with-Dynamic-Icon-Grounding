"""
Visual grounding using Gemini bounding box detection.

Pipeline per call:
  1. Send full-resolution screenshot to Gemini to get bounding box.
  2. Convert normalised 0-1000 coordinates to screen pixels.
  3. Return centre pixel coordinates, or retry on failure.
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

_GROUNDING_DIR = Path(__file__).parent.parent / "screenshoots" / "gemini"

# Cached API client — created once, reused across all calls
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. "
                "Set it with:  $env:GEMINI_API_KEY='your_key'  (PowerShell)"
            )
        _client = genai.Client(api_key=api_key)
    return _client


DETECTION_PROMPT = """\
Find the {target_description} on this Windows desktop screenshot.
Return its bounding box.

Return ONLY a JSON array:
[{{"box_2d": [y_min, x_min, y_max, x_max], "label": "icon name"}}]

Rules:
- box_2d values are integers 0-1000
- y comes before x
- Return [] if not found
- Return ONLY JSON, no markdown\
"""


def _strip_markdown(text: str) -> str:
    """Remove markdown code fences that Gemini sometimes wraps around JSON."""
    return re.sub(r"```[a-zA-Z]*", "", text).strip()


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


def _detect(client: genai.Client, screenshot: Image.Image, target: str) -> list[dict]:
    """Ask Gemini to return bounding boxes for the target icon."""
    prompt = DETECTION_PROMPT.format(target_description=target)
    raw = _call(client, [_img_part(screenshot, quality=90), types.Part(text=prompt)], temperature=0.7)
    print(f"  [gemini] detect response: {raw.strip()}")
    return json.loads(_strip_markdown(raw))


def _norm_to_pixels(box: tuple, W: int, H: int) -> tuple[int, int, int, int]:
    """Convert Gemini's 0-1000 normalised (y_min, x_min, y_max, x_max) to screen pixels."""
    y_min, x_min, y_max, x_max = box
    return (
        int((x_min / 1000) * W),
        int((y_min / 1000) * H),
        int((x_max / 1000) * W),
        int((y_max / 1000) * H),
    )


def _save_debug_image(screenshot: Image.Image, px_x1: int, px_y1: int, px_x2: int, px_y2: int, cx: int, cy: int, out_dir: Path | None = None) -> None:
    """Save an annotated screenshot with green bounding box and red crosshair."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_img = screenshot.copy()
    d = ImageDraw.Draw(result_img)
    d.rectangle([px_x1, px_y1, px_x2, px_y2], outline=(0, 255, 0), width=3)
    d.line([(cx - 20, cy), (cx + 20, cy)], fill=(255, 0, 0), width=3)
    d.line([(cx, cy - 20), (cx, cy + 20)], fill=(255, 0, 0), width=3)
    save_dir = out_dir if out_dir is not None else _GROUNDING_DIR
    save_dir.mkdir(parents=True, exist_ok=True)
    out_path = save_dir / f"detected_{timestamp}.png"
    result_img.save(out_path)
    print(f"  [grounding] saved → {out_path}")


def ground_icon(
    target_description: str,
    screenshot: Image.Image,
    max_retries: int = 3,
    save_debug: bool = True,
    debug_dir: Path | None = None,
) -> tuple[int, int] | None:
    """
    Locate a desktop icon using Gemini bounding box detection.

    Returns (x, y) screen pixel coordinates of the icon centre, or None.
    """
    client = _get_client()
    W, H = screenshot.size

    save_dir = debug_dir if debug_dir is not None else _GROUNDING_DIR
    if save_debug:
        save_dir.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, max_retries + 1):
        print(f"  [grounding] attempt {attempt}/{max_retries}")

        try:
            boxes = _detect(client, screenshot, target_description)

            if not boxes:
                print("  [gemini] no boxes returned")
                time.sleep(2)
                continue

            px_x1, px_y1, px_x2, px_y2 = _norm_to_pixels(tuple(boxes[0]["box_2d"]), W, H)

            print(f"  [gemini] box=({px_x1},{px_y1})→({px_x2},{px_y2}) "
                  f"label='{boxes[0].get('label', '')}'")

            if px_x2 <= px_x1 or px_y2 <= px_y1:
                print("  [gemini] degenerate box, retrying")
                time.sleep(2)
                continue

            cx = (px_x1 + px_x2) // 2
            cy = (px_y1 + px_y2) // 2
            print(f"  [grounding] confirmed at ({cx}, {cy})")

            if save_debug:
                _save_debug_image(screenshot, px_x1, px_y1, px_x2, px_y2, cx, cy, out_dir=save_dir)

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
