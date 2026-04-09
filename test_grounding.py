"""
Diagnostic test — saves an annotated image for every detection attempt.

Outputs:
  debug_screenshot.png          — raw screenshot sent to Gemini
  debug_attempt_1.png           — detected box + verify result on attempt 1
  debug_attempt_2.png           — attempt 2 (if retried)
  debug_attempt_3.png           — attempt 3 (if retried)
  debug_result.png              — final confirmed click point (if found)

Usage:
    $env:GEMINI_API_KEY="your_key"
    uv run python test_grounding.py
"""

import json
import os
import sys
import time

from PIL import Image, ImageDraw, ImageFont

from src.automation import NOTEPAD_TARGET, minimize_all_windows, take_screenshot
from src.grounding import MODEL, DETECTION_PROMPT, VERIFY_PROMPT, _detect, _verify
from google import genai


# ── Drawing helpers ──────────────────────────────────────────────────────────

def draw_box(img: Image.Image, x1, y1, x2, y2, color="lime", width=3) -> Image.Image:
    out = img.copy()
    ImageDraw.Draw(out).rectangle([x1, y1, x2, y2], outline=color, width=width)
    return out


def draw_crosshair(img: Image.Image, x: int, y: int, size: int = 20) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out)
    d.line([(x - size, y), (x + size, y)], fill="red", width=3)
    d.line([(x, y - size), (x, y + size)], fill="red", width=3)
    d.ellipse([(x - 6, y - 6), (x + 6, y + 6)], outline="red", width=2)
    return out


def draw_label(img: Image.Image, text: str, x: int, y: int, color="yellow") -> Image.Image:
    out = img.copy()
    ImageDraw.Draw(out).text((x, y), text, fill=color)
    return out


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY not set.")
        sys.exit(1)

    print("Minimising windows…")
    minimize_all_windows()

    print("Taking screenshot…")
    screenshot = take_screenshot()
    screenshot.save("debug_screenshot.png")
    W, H = screenshot.size
    print(f"Saved → debug_screenshot.png ({W}x{H})\n")

    print(f"Target: {NOTEPAD_TARGET}\n")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    max_retries = 3
    final_coord = None
    excluded_boxes: list[tuple] = []

    for attempt in range(1, max_retries + 1):
        print(f"{'='*50}")
        print(f"Attempt {attempt}/{max_retries}")
        print(f"{'='*50}")

        # Build exclusion text
        if excluded_boxes:
            excl_lines = "\n".join(f"  - {b}" for b in excluded_boxes)
            exclusions = (
                f"IMPORTANT: These regions were already checked and are WRONG — "
                f"do NOT return boxes in these areas:\n{excl_lines}\n"
                f"The target must be somewhere else on the screen.\n"
            )
        else:
            exclusions = ""

        # ── Detection ────────────────────────────────────────────────────────
        try:
            boxes = _detect(client, screenshot, NOTEPAD_TARGET, exclusions)
        except Exception as exc:
            print(f"  Detection error: {exc}")
            annotated = draw_label(screenshot, f"Attempt {attempt}: ERROR - {exc}", 10, 10)
            annotated.save(f"debug_attempt_{attempt}.png")
            time.sleep(2)
            continue

        if not boxes:
            print("  No boxes returned by Gemini")
            annotated = draw_label(screenshot, f"Attempt {attempt}: no boxes returned", 10, 10)
            annotated.save(f"debug_attempt_{attempt}.png")
            print(f"  Saved → debug_attempt_{attempt}.png")
            time.sleep(2)
            continue

        box = boxes[0]
        y_min, x_min, y_max, x_max = box["box_2d"]

        # Convert 0-1000 → pixels
        px_x1 = int((x_min / 1000) * W)
        px_y1 = int((y_min / 1000) * H)
        px_x2 = int((x_max / 1000) * W)
        px_y2 = int((y_max / 1000) * H)
        cx = (px_x1 + px_x2) // 2
        cy = (px_y1 + px_y2) // 2

        print(f"  Box pixels: ({px_x1},{px_y1}) → ({px_x2},{px_y2})")
        print(f"  Centre: ({cx},{cy})")
        print(f"  Label: '{box.get('label', '')}'")

        # ── Verification ─────────────────────────────────────────────────────
        pad = 10
        crop = screenshot.crop((
            max(0, px_x1 - pad), max(0, px_y1 - pad),
            min(W, px_x2 + pad), min(H, px_y2 + pad),
        ))
        verified = _verify(client, crop, NOTEPAD_TARGET) if px_x2 > px_x1 and px_y2 > px_y1 else False
        print(f"  Verified: {verified}")

        # ── Annotate and save attempt image ──────────────────────────────────
        box_color = "lime" if verified else "red"
        status_text = f"Attempt {attempt}: {'VERIFIED' if verified else 'WRONG'} | label='{box.get('label', '')}' | ({cx},{cy})"
        annotated = draw_box(screenshot, px_x1, px_y1, px_x2, px_y2, color=box_color)
        annotated = draw_crosshair(annotated, cx, cy)
        annotated = draw_label(annotated, status_text, 10, 10, color="yellow")

        fname = f"debug_attempt_{attempt}.png"
        annotated.save(fname)
        print(f"  Saved → {fname}  ({'GREEN' if verified else 'RED'} box)")

        if verified:
            final_coord = (cx, cy)
            break

        # Track failed region for next attempt
        excluded_boxes.append((y_min, x_min, y_max, x_max))
        time.sleep(2)

    # ── Final result ──────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    if final_coord:
        x, y = final_coord
        print(f"RESULT: Found at ({x}, {y})")
        result = draw_crosshair(screenshot, x, y)
        result.save("debug_result.png")
        print("Saved → debug_result.png")
    else:
        print("RESULT: Icon not found after all attempts")
        print("Open debug_attempt_*.png to see what Gemini detected each time")


if __name__ == "__main__":
    main()
