"""
Popup handling test — isolated from automation and grounding main flow.

How to use:
  1. Manually open a popup on screen (e.g. press Win+R to open the Run dialog)
  2. Run this script immediately
  3. It will call handle_popup_if_present and try to dismiss whatever is on screen

Usage:
    $env:GEMINI_API_KEY="your_key"
    uv run python test_popup.py
"""

import os
import sys

from src.automation import handle_popup_if_present, take_screenshot

if not os.environ.get("GEMINI_API_KEY"):
    print("ERROR: GEMINI_API_KEY not set.")
    sys.exit(1)

print("Taking screenshot...")
screenshot = take_screenshot()
screenshot.save("debug_popup_screenshot.png")
print("Saved → debug_popup_screenshot.png")

print("Looking for popup dismiss button...")
found = handle_popup_if_present(screenshot)

if found:
    print("SUCCESS: popup found and dismissed.")
else:
    print("NOT FOUND: no popup detected on screen.")
    print("Make sure a popup is visible before running this script.")
    print("Tip: press Win+R to open the Run dialog, then run this script.")
