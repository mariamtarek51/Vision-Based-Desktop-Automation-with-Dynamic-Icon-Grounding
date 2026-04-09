"""
Two-stage cascaded visual grounding using Groq API (Llama 4 Vision).

Stage 1 – Planner : full screenshot → coarse region [x1,y1,x2,y2] (normalized)
Stage 2 – Grounder: crop of that region → exact center (x,y) (normalized)

Works for ANY icon described in natural language.
"""

import base64
import json
import re
import time
from io import BytesIO

from groq import Groq
from PIL import Image

MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

# ── Prompts (exact spec) ────────────────────────────────────────────────────

PLANNER_PROMPT = """\
You are a desktop GUI analysis expert.
You are given a screenshot of a Windows desktop that may contain many small icons.
Your job is to carefully scan every icon visible in the image and locate the one matching the target.

Target: {target_description}

Instructions:
1. Scan the image systematically from top-left to bottom-right.
2. Identify every icon you can see and read their labels.
3. Find the one that matches the target description.
4. Return a bounding region with generous padding (at least 100px in each direction) around it.

Return ONLY this JSON, nothing else:
{{
  "found": true or false,
  "region": {{
    "x1_normalized": 0.0,
    "y1_normalized": 0.0,
    "x2_normalized": 0.5,
    "y2_normalized": 0.5
  }},
  "reasoning": "which icon you found and where it is on screen",
  "confidence": "high" or "medium" or "low"
}}
Rules:
- All values between 0.0 and 1.0
- Make the region LARGER than the icon — add padding so the grounder has context
- If you cannot confidently identify the target icon, set found to false
- Return ONLY JSON, no markdown, no explanation\
"""

GROUNDER_PROMPT = """\
You are a precise GUI element locator.
You are given a CROPPED region of a Windows desktop screenshot.
Find the EXACT center of the target icon in this crop.
Target: {target_description}
Return ONLY this JSON, nothing else:
{{
  "found": true or false,
  "x_normalized": 0.0,
  "y_normalized": 0.0,
  "confidence": "high" or "medium" or "low",
  "icon_label": "text label seen under icon"
}}
Rules:
- Coordinates relative to THIS CROPPED IMAGE only
- Point to CENTER of icon not the label beneath it
- If not found set found to false
- Return ONLY JSON, no markdown, no explanation\
"""

# ── Internal helpers ────────────────────────────────────────────────────────

def _to_base64(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _strip_markdown(text: str) -> str:
    """Remove ```json ... ``` fences and stray backticks defensively."""
    text = re.sub(r"```[a-zA-Z]*\s*", "", text)
    text = text.replace("```", "")
    return text.strip()


def _call_vlm(client: Groq, prompt: str, image: Image.Image) -> dict:
    """Send image + prompt to Groq VLM and return parsed JSON dict."""
    b64 = _to_base64(image)
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.0,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    raw = response.choices[0].message.content
    cleaned = _strip_markdown(raw)
    return json.loads(cleaned)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _resize_for_planner(img: Image.Image, max_width: int = 1280) -> Image.Image:
    """Scale width to max_width. Normalized coords are unaffected (ratio-based)."""
    w, h = img.size
    if w <= max_width:
        return img
    scale = max_width / w
    return img.resize((max_width, int(h * scale)), Image.LANCZOS)


def _make_tiles(
    img: Image.Image,
    rows: int = 4,
    cols: int = 5,
    overlap: float = 0.15,
) -> list[tuple[Image.Image, int, int]]:
    """
    Divide the screenshot into a rows×cols grid of overlapping tiles.
    Returns list of (tile_image, offset_x, offset_y).

    Why fine tiles instead of strips:
    - A 4×5 grid on 1920×1080 gives ~420×290px tiles.
    - Each tile contains ~3-5 icons — a trivial search for the model.
    - Strips (~640px wide) still have 15-20 icons and the planner still guesses wrong.
    - Tiles send directly to the GROUNDER (no planner coordinate-guessing needed).
    """
    W, H = img.size
    step_x = W / cols
    step_y = H / rows
    ovl_x = int(step_x * overlap)
    ovl_y = int(step_y * overlap)
    tiles = []
    for r in range(rows):
        for c in range(cols):
            x1 = max(0, int(c * step_x) - ovl_x)
            y1 = max(0, int(r * step_y) - ovl_y)
            x2 = min(W, int((c + 1) * step_x) + ovl_x)
            y2 = min(H, int((r + 1) * step_y) + ovl_y)
            tiles.append((img.crop((x1, y1, x2, y2)), x1, y1))
    return tiles


def _run_planner_grounder(
    client: Groq,
    planner_prompt: str,
    grounder_prompt: str,
    search_img: Image.Image,   # image sent to planner (may be a strip)
    full_screenshot: Image.Image,  # always the original — grounder crops from here
    offset_x: int,             # strip origin in full-screen coords
    offset_y: int,
) -> tuple[int, int] | None:
    """
    Run one planner→grounder pass on search_img.
    Crops for the grounder are taken from the original full_screenshot
    so the grounder always sees full-resolution pixels.
    Returns full-screen (x, y) or None.
    """
    W_full, H_full = full_screenshot.size
    W_search, H_search = search_img.size

    # ── Stage 1: Planner ────────────────────────────────────────────────────
    try:
        plan = _call_vlm(client, planner_prompt, _resize_for_planner(search_img))
    except (json.JSONDecodeError, Exception) as exc:
        print(f"  [planner] error: {exc}")
        return None

    if not plan.get("found", False):
        print(f"  [planner] not found – {plan.get('reasoning', '')}")
        return None

    print(f"  [planner] confidence={plan.get('confidence')} – {plan.get('reasoning', '')}")

    region = plan.get("region", {})
    # Planner coords are relative to search_img; remap to full-screen pixels
    rx1 = offset_x + int(_clamp(region.get("x1_normalized", 0.0)) * W_search)
    ry1 = offset_y + int(_clamp(region.get("y1_normalized", 0.0)) * H_search)
    rx2 = offset_x + int(_clamp(region.get("x2_normalized", 1.0)) * W_search)
    ry2 = offset_y + int(_clamp(region.get("y2_normalized", 1.0)) * H_search)

    # Clamp to full-screen bounds
    rx1, ry1 = max(0, rx1), max(0, ry1)
    rx2, ry2 = min(W_full, rx2), min(H_full, ry2)

    if rx2 <= rx1 or ry2 <= ry1:
        print("  [planner] degenerate region")
        return None

    # Grounder always crops from the original full-resolution screenshot
    crop = full_screenshot.crop((rx1, ry1, rx2, ry2))
    cw, ch = crop.size

    # ── Stage 2: Grounder ───────────────────────────────────────────────────
    try:
        ground = _call_vlm(client, grounder_prompt, crop)
    except (json.JSONDecodeError, Exception) as exc:
        print(f"  [grounder] error: {exc}")
        return None

    if not ground.get("found", False):
        print("  [grounder] not found in crop")
        return None

    print(f"  [grounder] confidence={ground.get('confidence')} label='{ground.get('icon_label', '')}'")

    lx = int(_clamp(ground.get("x_normalized", 0.5)) * cw)
    ly = int(_clamp(ground.get("y_normalized", 0.5)) * ch)
    return (rx1 + lx, ry1 + ly)


# ── Public API ──────────────────────────────────────────────────────────────

def ground_icon(
    target_description: str,
    screenshot: Image.Image,
    max_retries: int = 3,
) -> tuple[int, int] | None:
    """
    Two-stage cascaded grounding with strip-based fallback.

    Pass 1: planner searches the full screenshot.
    Pass 2 (fallback): planner searches each vertical strip separately.
                       Handles dense/cluttered desktops where the full image
                       overwhelms the model.

    Args:
        target_description: Natural-language description of the icon to find.
        screenshot:         PIL Image of the current desktop.
        max_retries:        Full pipeline attempts before giving up.

    Returns:
        (x, y) screen pixel coordinates of the icon centre, or None.
    """
    client = Groq()
    W, H = screenshot.size

    planner_prompt = PLANNER_PROMPT.format(target_description=target_description)
    grounder_prompt = GROUNDER_PROMPT.format(target_description=target_description)

    tiles = _make_tiles(screenshot, rows=4, cols=5, overlap=0.15)
    total = len(tiles)

    for attempt in range(1, max_retries + 1):
        print(f"  [grounding] attempt {attempt}/{max_retries}")

        # ── Pass 1: planner + grounder on full screenshot ────────────────────
        # Fast path — works when the desktop is less cluttered.
        print("  [grounding] pass 1 – full screenshot")
        result = _run_planner_grounder(
            client, planner_prompt, grounder_prompt,
            screenshot, screenshot, 0, 0,
        )
        if result:
            print(f"  [grounding] found at {result}")
            return result

        # ── Pass 2: grounder-only grid search ────────────────────────────────
        # The planner keeps guessing wrong coordinates on dense desktops.
        # Solution: skip the planner and send each small tile (~420×290px,
        # ~3-5 icons) directly to the grounder. The grounder only needs to
        # answer "is it here? if so, where?" — a trivial task on a small tile.
        print(f"  [grounding] pass 2 – tile grid search ({total} tiles, ~{screenshot.size[0]//5}×{screenshot.size[1]//4}px each)")
        for idx, (tile, ox, oy) in enumerate(tiles):
            print(f"  [tile {idx+1:02d}/{total}] offset=({ox},{oy}) size={tile.size}", end=" … ")
            try:
                ground = _call_vlm(client, grounder_prompt, tile)
            except (json.JSONDecodeError, Exception) as exc:
                print(f"error: {exc}")
                continue

            if not ground.get("found", False):
                print("not found")
                continue

            cw, ch = tile.size
            lx = int(_clamp(ground.get("x_normalized", 0.5)) * cw)
            ly = int(_clamp(ground.get("y_normalized", 0.5)) * ch)
            sx, sy = ox + lx, oy + ly
            print(f"FOUND — label='{ground.get('icon_label','')}' confidence={ground.get('confidence')} → screen ({sx},{sy})")
            return (sx, sy)

        print(f"  [grounding] attempt {attempt} exhausted – sleeping 1s")
        time.sleep(1)

    print("  [grounding] all attempts exhausted – returning None")
    return None
