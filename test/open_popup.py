"""
Opens a real Windows MessageBox popup to test popup dismissal handlers.

Run this in one terminal, then immediately run the popup test in another:

  Terminal 1:  uv run python test/open_popup.py
  Terminal 2:  uv run python -c "from src.template_grounding import dismiss_popup_win32; print(dismiss_popup_win32())"

The script blocks until the dialog is dismissed (either by the handler or manually).
"""

import ctypes
import time

MB_OKCANCEL    = 0x01   # OK + Cancel buttons
ICON_WARNING   = 0x30

print("Popup will appear in 5 seconds…")
time.sleep(5)

result = ctypes.windll.user32.MessageBoxW(
    0,
    "This is a test popup for the automation handler.\nIt should be dismissed automatically.",
    "Test Popup",
    MB_OKCANCEL | ICON_WARNING,
)
print(f"Dialog closed — button code: {result} (1=OK, 2=Cancel)")
