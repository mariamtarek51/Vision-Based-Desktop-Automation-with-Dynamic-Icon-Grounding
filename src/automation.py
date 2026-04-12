"""
Desktop automation helpers: window management, Notepad control, file saving.

All keyboard/mouse operations go through pyautogui.
Window queries go through win32gui (part of pywin32).
Clipboard writes go through win32clipboard (part of pywin32) so that
unicode text (API post bodies) pastes correctly into Notepad.
"""

import os
import subprocess
import time

import pyautogui
import win32clipboard
import win32con
import win32gui
from PIL import Image

# Slow down pyautogui slightly for reliability
pyautogui.PAUSE = 0.05

NOTEPAD_TARGET = "Windows Notepad application shortcut icon"


# ── Window helpers ───────────────────────────────────────────────────────────

def minimize_all_windows() -> None:
    """Minimise every open window to expose the desktop."""
    subprocess.run(
        ["powershell", "-Command",
         "(New-Object -ComObject Shell.Application).MinimizeAll()"],
        capture_output=True,
    )
    time.sleep(1.2)


def find_notepad_hwnd() -> int | None:
    """Return the HWND of the first visible Notepad window, or None."""
    found = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and "Notepad" in win32gui.GetWindowText(hwnd):
            found.append(hwnd)

    win32gui.EnumWindows(_cb, None)
    return found[0] if found else None


def wait_for_notepad(timeout: int = 10) -> bool:
    """Poll until a Notepad window appears or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if find_notepad_hwnd():
            return True
        time.sleep(0.3)
    return False


def focus_notepad() -> bool:
    """Bring the Notepad window to the foreground."""
    hwnd = find_notepad_hwnd()
    if hwnd:
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass  # silently fails from terminal; _click_notepad_center() is the reliable fallback
        time.sleep(0.4)
        return True
    return False


# ── Screenshot ───────────────────────────────────────────────────────────────

def take_screenshot() -> Image.Image:
    return pyautogui.screenshot()


# ── Clipboard ────────────────────────────────────────────────────────────────

def _set_clipboard(text: str) -> None:
    """Write unicode text to the Windows clipboard."""
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def _clear_clipboard() -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
    finally:
        win32clipboard.CloseClipboard()


# ── Notepad workflow ─────────────────────────────────────────────────────────

def launch_notepad(x: int, y: int) -> bool:
    """
    Double-click the desktop icon at (x, y) to launch Notepad.
    Returns True once the Notepad window is detected.
    """
    # Click empty desktop area first so the shell has focus
    pyautogui.click(10, 10)
    time.sleep(0.3)
    pyautogui.doubleClick(x, y)

    if not wait_for_notepad(timeout=3):
        print("  [notepad] timed out waiting for window")
        return False

    print("  [notepad] launched")
    time.sleep(0.8)  # let Notepad fully render
    return True


def _click_notepad_center() -> None:
    """Click the center of the Notepad window to guarantee keyboard focus."""
    hwnd = find_notepad_hwnd()
    if hwnd:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        cx = (left + right) // 2
        cy = (top + bottom) // 2
        pyautogui.click(cx, cy)
        time.sleep(0.3)


def type_post_content(title: str, body: str) -> None:
    """
    Paste formatted post content into the active Notepad window.
    Clears clipboard immediately after paste so it can't leak into Save As.
    """
    focus_notepad()
    _click_notepad_center()
    _set_clipboard(f"Title: {title}\n\n{body}")
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.5)
    _clear_clipboard()


def _wait_for_dialog(timeout: int = 8) -> bool:
    """
    Poll until the Windows common dialog (#32770) takes focus.
    Used for both Save As and the Replace File confirmation.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        fg = win32gui.GetForegroundWindow()
        if fg and win32gui.GetClassName(fg) == "#32770":
            return True
        time.sleep(0.2)
    return False


def save_as(filepath: str) -> None:
    """
    Save the current Notepad document to filepath via Save As dialog.
    Waits for the dialog to actually appear before typing the path.
    """
    file_existed = os.path.exists(filepath)

    focus_notepad()
    pyautogui.hotkey("ctrl", "s")

    if not _wait_for_dialog(timeout=8):
        print("  [save] WARNING: Save As dialog did not appear in time")
        return

    time.sleep(0.3)  # let dialog fully render

    # Clear filename field and type the full path
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    _set_clipboard(filepath)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(1.0)

    # "Replace existing file?" confirmation — only if file existed before.
    # The confirmation dialog is also class #32770 so wait for it to appear.
    # Default focused button is "No" so Tab moves focus to "Yes" before confirming.
    if file_existed:
        if _wait_for_dialog(timeout=3):
            pyautogui.press("tab")
            time.sleep(0.1)
            pyautogui.press("enter")
            time.sleep(0.5)

    _clear_clipboard()


def close_notepad() -> None:
    """Force-kill Notepad. File is already saved before this is called."""
    subprocess.run(["taskkill", "/f", "/im", "notepad.exe"], capture_output=True)
    time.sleep(0.5)


# ── Popup handling ───────────────────────────────────────────────────────────

def handle_popup_if_present(screenshot: Image.Image) -> bool:
    """
    Detect and dismiss any unexpected popup using the Gemini grounding
    pipeline — works for ANY popup without knowing its content in advance.
    Returns True if a popup was found and dismissed.

    Only used when method 1 (Gemini) is active. Import is deferred to
    avoid requiring google-genai when running template-only mode.
    """
    from src.grounding import ground_icon

    popup_description = (
        "a dismiss button on an unexpected popup, alert, or dialog box — "
        "could be labelled OK, Close, Cancel, Yes, No, or similar"
    )
    coord = ground_icon(popup_description, screenshot, max_retries=2, save_debug=False)
    if coord:
        x, y = coord
        print(f"  [popup] found dismiss button at ({x}, {y}), clicking")
        pyautogui.click(x, y)
        time.sleep(0.5)
        return True
    return False
