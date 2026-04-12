"""
Template matching grounding test.

Loads all notepad_icon_*.png templates from assets/, takes a desktop
screenshot, and runs template matching against all of them at scale=1.0.

Saves debug images so you can inspect what the matcher sees:
  debug_screenshot_edges.png  — edges extracted from the desktop screenshot
  debug_match_result.png      — annotated screenshot showing the best match

Usage:
    uv run python test/test_template_grounding.py
"""

import os
import sys
from datetime import datetime

import cv2
import numpy as np
import pyautogui
from PIL import Image, ImageDraw

from src.automation import minimize_all_windows, take_screenshot, wait_for_notepad
from src.template_grounding import _to_edges, ground_icon_template, load_all_templates

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")


def main() -> None:
    # ── Load all templates ───────────────────────────────────────────────────
    print("Loading templates…")
    try:
        templates = load_all_templates(ASSETS_DIR)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    # ── Take desktop screenshot and save its edge image ──────────────────────
    print("\nMinimising windows…")
    minimize_all_windows()

    print("Taking screenshot…")
    screenshot = take_screenshot()
    screenshot_gray  = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2GRAY)
    screenshot_edges = _to_edges(screenshot_gray)
    cv2.imwrite("debug_screenshot_edges.png", screenshot_edges)
    print(f"  Screenshot: {screenshot.size[0]}x{screenshot.size[1]}")
    print("  Saved → debug_screenshot_edges.png")

    # ── Run template matching ────────────────────────────────────────────────
    print("\nRunning template matching…")
    coord = ground_icon_template(templates, screenshot)

    if coord is None:
        print("\nFAILED: icon not found.")
        print("Check debug_screenshot_edges.png — is the Notepad icon visible?")
        sys.exit(1)

    x, y = coord

    # ── Annotate result on original screenshot ───────────────────────────────
    out_dir = os.path.join(os.path.dirname(__file__), "..", "screenshoots", "templateMatching")
    os.makedirs(out_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"matched_{timestamp}.png")

    result_img = screenshot.copy()
    d = ImageDraw.Draw(result_img)
    d.line([(x - 20, y), (x + 20, y)], fill="red", width=2)
    d.line([(x, y - 20), (x, y + 20)], fill="red", width=2)
    result_img.save(out_path)
    print(f"  Saved → {out_path}")

    print(f"\nFound at: ({x}, {y})")
    print(f"Double-clicking ({x}, {y})…")
    pyautogui.click(10, 10)
    pyautogui.doubleClick(x, y)

    if wait_for_notepad(timeout=10):
        print("SUCCESS: Notepad opened.")
    else:
        print("WARNING: Notepad did not open within 10 seconds.")


if __name__ == "__main__":
    main()
