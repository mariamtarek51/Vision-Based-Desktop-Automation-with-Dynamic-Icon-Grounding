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
import winreg

import pyautogui
import win32clipboard
import win32con
import win32gui
from PIL import Image

from src.grounding import ground_icon

# Slow down pyautogui slightly for reliability; disable corner-failsafe.
pyautogui.PAUSE = 0.05
pyautogui.FAILSAFE = False
#"Notepad icon — a Windows text editor application, typically showing "
#    "a notepad or paper with lines, possibly labeled 'Notepad' underneath"
NOTEPAD_TARGET = (
    "Notepad shortcut icon on the Windows desktop — a spiral-bound notebook "
    "with a light blue/teal cover and horizontal ruled white lines on the page, "
    "with a small blue Windows shortcut arrow overlaid in the bottom-left corner "
    "of the icon. It may be labeled 'Notepad' in small text underneath. "
    "It is distinct from folder icons, PDF icons, and browser icons."
)


# ── Desktop path ────────────────────────────────────────────────────────────

def get_desktop_path() -> str:
    """
    Resolve the real Desktop folder (handles OneDrive-redirected desktops).
    Falls back to ~/Desktop if the registry key is absent.
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        )
        desktop, _ = winreg.QueryValueEx(key, "Desktop")
        winreg.CloseKey(key)
        return desktop
    except OSError:
        return os.path.join(os.path.expanduser("~"), "Desktop")


# ── Window helpers ───────────────────────────────────────────────────────────

def minimize_all_windows() -> None:
    """Minimise every open window to expose the desktop."""
    subprocess.run(
        ["powershell", "-Command",
         "(New-Object -ComObject Shell.Application).MinimizeAll()"],
        capture_output=True,
    )
    time.sleep(1.2)  # give Windows time to animate


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
        time.sleep(0.4)
    return False


def focus_notepad() -> bool:
    """Bring the Notepad window to the foreground."""
    hwnd = find_notepad_hwnd()
    if hwnd:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        return True
    return False


def notepad_is_open() -> bool:
    return find_notepad_hwnd() is not None


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


# ── Notepad workflow ─────────────────────────────────────────────────────────

def launch_notepad(x: int, y: int) -> bool:
    """
    Double-click the desktop icon at (x, y) and wait for Notepad to open.
    Returns True if Notepad window was detected within 10 seconds.
    """
    pyautogui.doubleClick(x, y)
    launched = wait_for_notepad(timeout=10)
    if launched:
        print(f"  [notepad] launched (window detected)")
        time.sleep(0.5)  # let Notepad fully render
    else:
        print("  [notepad] timed out waiting for window")
    return launched


def type_post_content(title: str, body: str) -> None:
    """
    Paste the formatted post content into the active Notepad window.
    Uses the clipboard so unicode characters survive intact.
    """
    focus_notepad()
    content = f"Title: {title}\n\n{body}"
    _set_clipboard(content)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)


def save_as(filepath: str) -> None:
    """
    Trigger Save As (Ctrl+S on an unsaved document), type the full file
    path into the dialog's filename field, and confirm.
    Also handles the 'replace existing file?' prompt that may follow.
    """
    pyautogui.hotkey("ctrl", "s")
    time.sleep(1.2)  # wait for Save As dialog to open

    # The filename field is focused by default in the Save As dialog.
    # Select-all clears whatever default name is there, then paste the path.
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)

    # Use clipboard to paste path (handles spaces and special chars)
    _set_clipboard(filepath)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.2)

    pyautogui.press("enter")
    time.sleep(0.8)

    # Handle "File already exists – replace?" dialog (press Enter = Yes)
    pyautogui.press("enter")
    time.sleep(0.5)


def close_notepad() -> None:
    """
    Close Notepad with Alt+F4.
    If a 'Do you want to save?' prompt appears, dismiss it without saving
    (the file was already saved explicitly before this call).
    """
    hwnd = find_notepad_hwnd()
    if not hwnd:
        return

    focus_notepad()
    pyautogui.hotkey("alt", "f4")
    time.sleep(0.8)

    # If Notepad is still open, a save-prompt appeared – press Tab then Enter
    # to select "Don't Save" (Tab moves from Save → Don't Save → Cancel).
    if notepad_is_open():
        pyautogui.press("tab")   # move to "Don't Save"
        time.sleep(0.1)
        pyautogui.press("enter")
        time.sleep(0.5)


# ── Popup handling ───────────────────────────────────────────────────────────

def handle_popup_if_present(screenshot: Image.Image) -> bool:
    """
    Detect and dismiss any unexpected popup or dialog using the grounding
    pipeline — works for ANY popup without knowing its content in advance.
    Returns True if a popup was found and dismissed.
    """
    popup_description = (
        "a dismiss button on an unexpected popup, alert, or dialog box — "
        "could be labelled OK, Close, Cancel, Yes, No, or similar"
    )
    coord = ground_icon(popup_description, screenshot, max_retries=2)
    if coord:
        x, y = coord
        print(f"  [popup] found dismiss button at ({x}, {y}), clicking")
        pyautogui.click(x, y)
        time.sleep(0.5)
        return True
    return False
