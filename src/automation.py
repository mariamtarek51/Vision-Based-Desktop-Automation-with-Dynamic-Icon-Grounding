"""
Desktop automation helpers: window management, Notepad control, file saving.

Keyboard/mouse → pyautogui
Window queries  → win32gui (pywin32)
Clipboard       → win32clipboard (pywin32)
"""

import os
import subprocess
import time
from pathlib import Path

import pyautogui
import win32clipboard
import win32con
import win32gui
import win32process
from PIL import Image

pyautogui.PAUSE = 0.05

NOTEPAD_TARGET = "Windows Notepad application shortcut icon"


# ── Window helpers ───────────────────────────────────────────────────────────

def minimize_all_windows() -> None:
    subprocess.run(
        ["powershell", "-Command",
         "(New-Object -ComObject Shell.Application).MinimizeAll()"],
        capture_output=True,
    )
    time.sleep(1.2)


def find_notepad_hwnd() -> int | None:
    found = []
    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and "Notepad" in win32gui.GetWindowText(hwnd):
            found.append(hwnd)
    win32gui.EnumWindows(_cb, None)
    return found[0] if found else None


def wait_for_notepad(timeout: int = 10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if find_notepad_hwnd():
            return True
        time.sleep(0.3)
    return False


def focus_notepad() -> bool:
    hwnd = find_notepad_hwnd()
    if not hwnd:
        return False
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.4)
    return True


# ── Screenshot ───────────────────────────────────────────────────────────────

def take_screenshot() -> Image.Image:
    return pyautogui.screenshot()


# ── Clipboard ────────────────────────────────────────────────────────────────

def _clipboard(text: str | None = None, retries: int = 5) -> None:
    """Set clipboard to `text`, or clear it if `text` is None.

    Retries if another application holds the clipboard open.
    """
    for attempt in range(retries):
        try:
            win32clipboard.OpenClipboard()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(0.1)
            continue
        try:
            win32clipboard.EmptyClipboard()
            if text is not None:
                win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return


def _paste(text: str) -> None:
    _clipboard(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)


# ── Notepad workflow ─────────────────────────────────────────────────────────

def launch_notepad(x: int, y: int) -> bool:
    pyautogui.doubleClick(x, y)
    if not wait_for_notepad(timeout=5):
        print("  [notepad] timed out waiting for window")
        return False
    print("  [notepad] launched")
    time.sleep(0.8)
    return True


def _click_notepad_center() -> None:
    hwnd = find_notepad_hwnd()
    if hwnd:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        pyautogui.click((left + right) // 2, (top + bottom) // 2)
        time.sleep(0.3)


def type_post_content(title: str, body: str) -> None:
    focus_notepad()
    _click_notepad_center()
    _paste(f"Title: {title}\n\n{body}")
    time.sleep(0.2)
    _clipboard()


def _wait_for_dialog(timeout: int = 8) -> bool:
    """Wait until a #32770 dialog (Save As / confirmation) takes focus."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        fg = win32gui.GetForegroundWindow()
        if fg and win32gui.GetClassName(fg) == "#32770":
            return True
        time.sleep(0.2)
    return False


def save_as(filepath: str) -> None:
    file_existed = os.path.exists(filepath)

    focus_notepad()
    pyautogui.hotkey("ctrl", "s")

    if not _wait_for_dialog(timeout=10):
        print("  [save] WARNING: Save As dialog did not appear in time")
        return

    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    _paste(filepath)
    pyautogui.press("enter")
    time.sleep(1.0)

    # "Replace existing file?" confirmation
    if file_existed and _wait_for_dialog(timeout=3):
        pyautogui.press("tab")
        time.sleep(0.1)
        pyautogui.press("enter")
        time.sleep(0.5)

    _clipboard()


def close_notepad() -> None:
    """Force-kill the Notepad instance we launched (by PID), not all Notepad windows."""
    hwnd = find_notepad_hwnd()
    if hwnd:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        subprocess.run(["taskkill", "/f", "/pid", str(pid)], capture_output=True)
    else:
        # Fallback: no window found, kill by image name as last resort
        subprocess.run(["taskkill", "/f", "/im", "notepad.exe"], capture_output=True)
    time.sleep(0.5)


# ── Popup handling ───────────────────────────────────────────────────────────

def handle_popup_if_present(screenshot: Image.Image) -> bool:
    """Detect and dismiss any popup via Gemini vision. Method 1 only."""
    fg = win32gui.GetForegroundWindow()
    if fg and win32gui.GetClassName(fg) == "#32770":
        notepad_hwnd = find_notepad_hwnd()
        if win32gui.GetWindow(fg, win32con.GW_OWNER) == notepad_hwnd:
            print("  [popup] foreground dialog belongs to Notepad — skipping")
            return False

    from src.grounding import ground_icon

    coord = ground_icon(
        "a dismiss button on an unexpected popup, alert, or dialog box — "
        "could be labelled OK, Close, Cancel, Yes, No, or similar",
        screenshot,
        max_retries=2,
        save_debug=True,
        debug_dir=Path(__file__).parent.parent / "screenshoots" / "gemini" / "popup",
    )
    if coord:
        print(f"  [popup] found dismiss button at {coord}, clicking")
        pyautogui.click(*coord)
        time.sleep(0.5)
        return True
    return False
