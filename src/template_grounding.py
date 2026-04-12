"""
Template matching grounding — offline alternative to Gemini vision.

Uses Canny edge detection before matching:
  - Edges are the icon's outlines and internal features (shape only)
  - Background pixels produce no edges → wallpaper is invisible to the matcher
  - Works with transparent-background PNGs and any desktop theme/wallpaper
  - No reference image recapture needed when the icon moves

Multiple pre-captured templates (one per icon size setting) are tried at
scale=1.0 — no multi-scale loop needed because Windows renders different
artwork at each size class, not a scaled version of one image.

No API calls, no quota limits.
"""

import glob
import os
import time
from datetime import datetime

import cv2
import numpy as np
import win32con
import win32gui
from PIL import Image, ImageDraw

# Canny edge detection thresholds
_CANNY_LOW  = 50
_CANNY_HIGH = 150


def _to_edges(gray: np.ndarray) -> np.ndarray:
    """Blur slightly then apply Canny edge detection."""
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.Canny(blurred, _CANNY_LOW, _CANNY_HIGH)


def load_template(icon_path: str) -> np.ndarray:
    """
    Load a reference icon image from disk and convert it to grayscale.

    If the PNG has a transparent background, transparent pixels are composited
    onto neutral grey (128) so no spurious boundary edges appear when Canny runs.
    Grey is chosen because it creates the smallest gradient adjacent to most icon
    colours, minimising false edges at the icon/background boundary.

    Returns a 2D grayscale numpy array ready for ground_icon_template.
    Raises ValueError if the image cannot be loaded.
    """
    img = cv2.imread(icon_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"could not load image from {icon_path}")

    if img.ndim == 3 and img.shape[2] == 4:
        # Transparent PNG: composite onto grey before converting to grayscale
        alpha   = img[:, :, 3:4].astype(float) / 255.0
        bgr     = img[:, :, :3].astype(float)
        grey_bg = np.full_like(bgr, 128.0)
        img = (bgr * alpha + grey_bg * (1 - alpha)).astype(np.uint8)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape
    print(f"  [template] loaded {os.path.basename(icon_path)}: {w}x{h} px")
    return gray


def load_all_templates(assets_dir: str) -> list[np.ndarray]:
    """
    Load every notepad_icon_*.png in assets_dir and return a list of
    grayscale arrays. Each file is a template captured at a specific
    desktop icon size (small, medium, large).

    Raises ValueError if no matching files are found.
    """
    pattern = os.path.join(assets_dir, "notepad_icon_*.png")
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise ValueError(
            f"No notepad_icon_*.png files found in {assets_dir}.\n"
            "Run capture_icon.py once per icon size to create them."
        )
    templates = []
    for p in paths:
        print(f"  Loading {os.path.basename(p)}…")
        templates.append(load_template(p))
    print(f"  [template] {len(templates)} template(s) loaded")
    return templates


def ground_icon_template(
    templates: list[np.ndarray],
    screenshot: Image.Image,
    threshold: float = 0.5,
) -> tuple[int, int] | None:
    """
    Locate a desktop icon by trying each pre-captured template at scale=1.0.

    Windows renders different artwork for each icon size class, so a single
    template cannot be rescaled to match all sizes. Instead, one template per
    size is captured in advance. At runtime each is matched against the
    screenshot and the highest-scoring one (if above threshold) is accepted.

    Args:
        templates:  List of grayscale icon images (from load_all_templates).
        screenshot: PIL Image of the current desktop.
        threshold:  Minimum TM_CCOEFF_NORMED score to accept a match (0–1).

    Returns:
        (x, y) screen pixel coordinates of the icon centre, or None if not found.
    """
    screenshot_gray  = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2GRAY)
    screenshot_edges = _to_edges(screenshot_gray)
    scr_h, scr_w = screenshot_edges.shape

    best_val = -1.0
    best_loc = None
    best_w, best_h = 0, 0

    for template_gray in templates:
        tmpl_h, tmpl_w = template_gray.shape
        if tmpl_w >= scr_w or tmpl_h >= scr_h:
            continue

        template_edges = _to_edges(template_gray)
        result = cv2.matchTemplate(screenshot_edges, template_edges, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        print(f"  [template] {tmpl_w}x{tmpl_h} px → confidence: {max_val:.3f}")

        if max_val > best_val:
            best_val = max_val
            best_loc = max_loc
            best_w, best_h = tmpl_w, tmpl_h

    if best_val < threshold or best_loc is None:
        print(f"  [template] no match found (best confidence: {best_val:.3f}, threshold: {threshold})")
        return None

    cx = best_loc[0] + best_w // 2
    cy = best_loc[1] + best_h // 2
    print(f"  [template] matched at ({cx}, {cy}) — confidence: {best_val:.3f}")

    out_dir = os.path.join(os.path.dirname(__file__), "..", "screenshoots", "templateMatching")
    os.makedirs(out_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"matched_{timestamp}.png")
    result_img = screenshot.copy()
    d = ImageDraw.Draw(result_img)
    d.line([(cx - 20, cy), (cx + 20, cy)], fill="red", width=2)
    d.line([(cx, cy - 20), (cx, cy + 20)], fill="red", width=2)
    result_img.save(out_path)
    print(f"  [template] saved → {out_path}")

    return (cx, cy)


def dismiss_popup_win32() -> bool:
    """
    Detect and dismiss an unexpected popup using Win32 API button enumeration.
    No vision model required.

    Checks if the foreground window is not Notepad — if so, treats it as a popup
    and clicks the first dismiss button (OK, Yes, Close, Cancel) found in it.

    Returns True if a popup was found and dismissed.
    """
    import time as _time

    t0 = _time.perf_counter()

    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return False
    title = win32gui.GetWindowText(hwnd)

    if not title or "Notepad" in title or hwnd == win32gui.GetDesktopWindow():
        return False

    t_detected = _time.perf_counter()
    print(f"  [popup-win32] detected '{title}' in {(t_detected - t0)*1000:.1f} ms")

    buttons = []

    def _enum_buttons(child_hwnd, _):
        if win32gui.GetClassName(child_hwnd) == "Button":
            btn_text = win32gui.GetWindowText(child_hwnd).strip().lower()
            buttons.append((child_hwnd, btn_text))

    win32gui.EnumChildWindows(hwnd, _enum_buttons, None)

    t_enumerated = _time.perf_counter()
    print(f"  [popup-win32] enumerated {len(buttons)} button(s) in {(t_enumerated - t_detected)*1000:.1f} ms")

    for label in ("ok", "yes", "close", "cancel"):
        for btn_hwnd, btn_text in buttons:
            if btn_text == label:
                win32gui.PostMessage(btn_hwnd, win32con.BM_CLICK, 0, 0)
                t_clicked = _time.perf_counter()
                print(f"  [popup-win32] clicked '{btn_text}' in {(t_clicked - t_enumerated)*1000:.1f} ms  |  total: {(t_clicked - t0)*1000:.1f} ms")
                return True

    if buttons:
        btn_hwnd, btn_text = buttons[0]
        win32gui.PostMessage(btn_hwnd, win32con.BM_CLICK, 0, 0)
        t_clicked = _time.perf_counter()
        print(f"  [popup-win32] clicked '{btn_text}' in {(t_clicked - t_enumerated)*1000:.1f} ms  |  total: {(t_clicked - t0)*1000:.1f} ms")
        return True

    print("  [popup-win32] no buttons found in popup window")
    return False
