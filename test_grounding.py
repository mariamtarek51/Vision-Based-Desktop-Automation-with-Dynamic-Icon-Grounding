"""
Diagnostic test for the two-stage grounding pipeline.

Saves every image sent to the VLM and every raw model response so you can
pinpoint exactly where the pipeline breaks:

  debug_full.png          — full screenshot sent to the planner (pass 1)
  debug_strip_0.png       — strip 0 sent to planner (pass 2)
  debug_strip_1.png       — strip 1
  debug_strip_2.png       — strip 2
  debug_planner_crop.png  — crop the grounder receives (from planner region)
  debug_result.png        — final screenshot with red crosshair (if found)

Console output includes every raw VLM response so you can read exactly
what the model said.

Usage:
    uv run python test_grounding.py
"""

import base64
import json
import os
import re
import sys
import time
from io import BytesIO

from groq import Groq
from PIL import Image, ImageDraw

from src.automation import NOTEPAD_TARGET, minimize_all_windows, take_screenshot

# ── Config ──────────────────────────────────────────────────────────────────

MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

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

# ── Helpers ──────────────────────────────────────────────────────────────────

def to_b64(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def strip_md(text: str) -> str:
    text = re.sub(r"```[a-zA-Z]*\s*", "", text)
    return text.replace("```", "").strip()


def call_vlm(client: Groq, prompt: str, img: Image.Image, label: str) -> dict | None:
    """Call the VLM, print the raw response, return parsed dict or None."""
    print(f"\n  >>> sending to VLM [{label}] (image size: {img.size})")
    raw = ""
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=0.0,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{to_b64(img)}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = resp.choices[0].message.content
        print(f"  <<< raw response [{label}]:\n{raw}\n")
        return json.loads(strip_md(raw))
    except json.JSONDecodeError:
        print(f"  [!] JSON parse failed for response:\n{raw}")
        return None
    except Exception as exc:
        print(f"  [!] API error: {exc}")
        return None


def clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def draw_crosshair(img: Image.Image, x: int, y: int, color="red", size=20) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out)
    d.line([(x - size, y), (x + size, y)], fill=color, width=3)
    d.line([(x, y - size), (x, y + size)], fill=color, width=3)
    d.ellipse([(x - 6, y - 6), (x + 6, y + 6)], outline=color, width=2)
    return out


def draw_box(img: Image.Image, x1, y1, x2, y2, color="lime") -> Image.Image:
    out = img.copy()
    ImageDraw.Draw(out).rectangle([x1, y1, x2, y2], outline=color, width=3)
    return out


def resize_for_planner(img: Image.Image, max_width: int = 1280) -> Image.Image:
    w, h = img.size
    if w <= max_width:
        return img
    scale = max_width / w
    return img.resize((max_width, int(h * scale)), Image.LANCZOS)


# ── Diagnostic pipeline ───────────────────────────────────────────────────────

def diagnose(target: str, screenshot: Image.Image, client: Groq) -> None:
    W, H = screenshot.size
    planner_prompt = PLANNER_PROMPT.format(target_description=target)
    grounder_prompt = GROUNDER_PROMPT.format(target_description=target)

    print("=" * 60)
    print(f"TARGET: {target}")
    print(f"SCREENSHOT SIZE: {W}x{H}")
    print("=" * 60)

    # ── Pass 1: full screenshot ──────────────────────────────────────────────
    print("\n[PASS 1] Planner on full screenshot")
    planner_img = resize_for_planner(screenshot)
    planner_img.save("debug_full.png")
    print(f"  saved → debug_full.png ({planner_img.size[0]}x{planner_img.size[1]})")

    plan = call_vlm(client, planner_prompt, planner_img, "planner-full")

    if plan and plan.get("found"):
        _try_grounder(plan, screenshot, grounder_prompt, client, 0, 0, "pass1")
        return

    # ── Pass 2: tile grid — grounder directly on each small tile ─────────────
    print("\n[PASS 2] Tile grid search (4×5 = 20 tiles, ~420×270px each)")
    print("  Skipping planner — sending each tile directly to grounder.")
    print("  Tiles saved to debug_tiles/ so you can see what the model sees.\n")

    os.makedirs("debug_tiles", exist_ok=True)

    rows, cols, overlap = 4, 5, 0.15
    step_x, step_y = W / cols, H / rows
    ovl_x, ovl_y = int(step_x * overlap), int(step_y * overlap)
    total = rows * cols

    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c
            x1 = max(0, int(c * step_x) - ovl_x)
            y1 = max(0, int(r * step_y) - ovl_y)
            x2 = min(W, int((c + 1) * step_x) + ovl_x)
            y2 = min(H, int((r + 1) * step_y) + ovl_y)
            tile = screenshot.crop((x1, y1, x2, y2))

            fname = f"debug_tiles/tile_{idx:02d}_r{r}c{c}.png"
            tile.save(fname)
            print(f"  [tile {idx+1:02d}/{total}] ({x1},{y1})→({x2},{y2}) size={tile.size}", end=" … ")

            result = call_vlm(client, grounder_prompt, tile, f"grounder-tile{idx}")
            if result and result.get("found"):
                cw, ch = tile.size
                lx = int(clamp(result.get("x_normalized", 0.5)) * cw)
                ly = int(clamp(result.get("y_normalized", 0.5)) * ch)
                sx, sy = x1 + lx, y1 + ly
                print(f"FOUND → screen ({sx},{sy})")

                annotated = draw_crosshair(screenshot, sx, sy)
                annotated.save("debug_result.png")
                print(f"\n[RESULT] Icon found at ({sx},{sy})")
                print(f"  saved → debug_result.png")
                print(f"  tile that matched → {fname}")
                return
            else:
                print("not found")

    print("\n[RESULT] Icon NOT found after all passes.")
    print("\nDIAGNOSIS GUIDE:")
    print("  1. Open debug_tiles/ — find the tile that contains the Notepad icon")
    print("     → If you can see it: TARGET DESCRIPTION needs improvement")
    print("     → If it's not in any tile: icon is not on the desktop")
    print("  2. Check the raw VLM responses above for that tile")
    print("     → 'found: false' = description mismatch")


def _try_grounder(
    plan: dict,
    screenshot: Image.Image,
    grounder_prompt: str,
    client: Groq,
    offset_x: int,
    offset_y: int,
    label: str,
) -> bool:
    W, H = screenshot.size
    region = plan.get("region", {})

    # Strip-relative → full-screen pixel coords
    strip_w = screenshot.crop((offset_x, offset_y, W, H)).size[0] if offset_x else W
    strip_h = H

    rx1 = offset_x + int(clamp(region.get("x1_normalized", 0.0)) * strip_w)
    ry1 = offset_y + int(clamp(region.get("y1_normalized", 0.0)) * strip_h)
    rx2 = offset_x + int(clamp(region.get("x2_normalized", 1.0)) * strip_w)
    ry2 = offset_y + int(clamp(region.get("y2_normalized", 1.0)) * strip_h)

    rx1, ry1 = max(0, rx1), max(0, ry1)
    rx2, ry2 = min(W, rx2), min(H, ry2)

    print(f"  [planner] region pixels: ({rx1},{ry1}) → ({rx2},{ry2})")

    if rx2 <= rx1 or ry2 <= ry1:
        print("  [!] Degenerate region — planner returned bad coordinates")
        return False

    # Show the planner region on the screenshot
    annotated = draw_box(screenshot, rx1, ry1, rx2, ry2, color="lime")
    annotated.save("debug_planner_region.png")
    print("  saved → debug_planner_region.png  (green box = planner region)")

    crop = screenshot.crop((rx1, ry1, rx2, ry2))
    crop.save("debug_planner_crop.png")
    print(f"  saved → debug_planner_crop.png ({crop.size[0]}x{crop.size[1]})  ← sent to grounder")

    ground = call_vlm(client, grounder_prompt, crop, f"grounder-{label}")
    if not ground or not ground.get("found"):
        print("  [grounder] icon not found in crop")
        return False

    cw, ch = crop.size
    lx = int(clamp(ground.get("x_normalized", 0.5)) * cw)
    ly = int(clamp(ground.get("y_normalized", 0.5)) * ch)
    sx, sy = rx1 + lx, ry1 + ly

    print(f"\n[RESULT] Found at screen ({sx}, {sy})")
    result_img = draw_crosshair(screenshot, sx, sy)
    result_img.save("debug_result.png")
    print("  saved → debug_result.png  (red crosshair = click point)")
    return True


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not os.environ.get("GROQ_API_KEY"):
        print("ERROR: GROQ_API_KEY not set.")
        sys.exit(1)

    print("Minimising windows…")
    minimize_all_windows()

    print("Taking screenshot…")
    screenshot = take_screenshot()
    screenshot.save("debug_screenshot.png")
    print(f"saved → debug_screenshot.png\n")

    client = Groq()
    diagnose(NOTEPAD_TARGET, screenshot, client)


if __name__ == "__main__":
    main()
