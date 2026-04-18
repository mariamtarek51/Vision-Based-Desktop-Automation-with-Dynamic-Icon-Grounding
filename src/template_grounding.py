"""
Template-matching grounding — offline alternative to Gemini vision.

Method 2 (ground_icon_template):
  Multiple pre-captured templates (one per icon size) matched at scale=1.0
  using Canny edge detection + TM_CCOEFF_NORMED.

Method 3 (find_icon_twoGates):
  Single reference image matched via a two-gate pipeline: coarse multi-scale
  edge search → edge hit-rate verification.

Also provides dismiss_popup_win32() for closing unexpected dialogs via Win32.
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CANNY_LOW = 50
_CANNY_HIGH = 150
_DILATE_KERNEL = np.ones((3, 3), np.uint8)
_ERODE_KERNEL = np.ones((5, 5), np.uint8)

# ---------------------------------------------------------------------------
# Shared edge helpers
# ---------------------------------------------------------------------------

def _to_edges(gray: np.ndarray) -> np.ndarray:
    """Gaussian blur → Canny edge detection."""
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.Canny(blurred, _CANNY_LOW, _CANNY_HIGH)


def _soft_edges(gray: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    """Blur → optional mask → low-threshold Canny → dilate (more tolerant edges)."""
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    if mask is not None:
        blurred = cv2.bitwise_and(blurred, blurred, mask=mask)
    edges = cv2.Canny(blurred, 30, 100)
    return cv2.dilate(edges, _DILATE_KERNEL, iterations=1)


def _edge_hit_rate(
    template_edges: np.ndarray,
    candidate_edges: np.ndarray,
    mask: np.ndarray | None = None,
) -> float:
    """Fraction of template edge pixels (inside mask) that overlap candidate edges.

    Unlike TM_CCOEFF_NORMED this is a plain ratio — sparse images cannot inflate it.
    """
    templ_px = template_edges > 0
    if mask is not None:
        templ_px = templ_px & (mask > 0)
    total = int(templ_px.sum())
    if total == 0:
        return 0.0
    return float((templ_px & (candidate_edges > 0)).sum()) / total


def _composite_on_grey(bgra: np.ndarray) -> np.ndarray:
    """Composite a BGRA image onto neutral grey (128) → return BGR uint8.

    Grey minimises false Canny edges at the icon/background boundary.
    """
    alpha = bgra[:, :, 3:4].astype(float) / 255.0
    bgr = bgra[:, :, :3].astype(float)
    return (bgr * alpha + 128.0 * (1.0 - alpha)).astype(np.uint8)


def _save_debug_image(
    screenshot: Image.Image, cx: int, cy: int, out_dir: str,
) -> None:
    """Save a copy of the screenshot with a red crosshair at (cx, cy)."""
    os.makedirs(out_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = screenshot.copy()
    draw = ImageDraw.Draw(result)
    draw.line([(cx - 20, cy), (cx + 20, cy)], fill="red", width=2)
    draw.line([(cx, cy - 20), (cx, cy + 20)], fill="red", width=2)
    path = os.path.join(out_dir, f"matched_{timestamp}.png")
    result.save(path)
    print(f"  [template] saved → {path}")


# ---------------------------------------------------------------------------
# Method 2 — Multi-template matching
# ---------------------------------------------------------------------------

def load_template(icon_path: str) -> np.ndarray:
    """Load a reference icon PNG and return a grayscale array.

    Transparent pixels are composited onto grey (128) before conversion.
    """
    img = cv2.imread(icon_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"could not load image from {icon_path}")

    if img.ndim == 3 and img.shape[2] == 4:
        img = _composite_on_grey(img)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape
    print(f"  [template] loaded {os.path.basename(icon_path)}: {w}x{h} px")
    return gray


def load_all_templates(assets_dir: str) -> list[np.ndarray]:
    """Load every ``notepad_icon_*.png`` in *assets_dir* as grayscale arrays.

    Raises ValueError if no matching files are found.
    """
    pattern = os.path.join(assets_dir, "notepad_icon_*.png")
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise ValueError(
            f"No notepad_icon_*.png files found in {assets_dir}.\n"
            "Run capture_icon.py once per icon size to create them."
        )
    templates = [load_template(p) for p in paths]
    print(f"  [template] {len(templates)} template(s) loaded")
    return templates


def ground_icon_template(
    templates: list[np.ndarray],
    screenshot: Image.Image,
    threshold: float = 0.5,
) -> tuple[int, int] | None:
    """Locate a desktop icon by trying each template at scale=1.0 (Method 2).

    Returns (x, y) centre of the best match, or None if below *threshold*.
    """
    scr_gray = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2GRAY)
    scr_edges = _to_edges(scr_gray)
    scr_h, scr_w = scr_edges.shape

    best_val, best_loc, best_w, best_h = -1.0, None, 0, 0

    for tmpl_gray in templates:
        th, tw = tmpl_gray.shape
        if tw >= scr_w or th >= scr_h:
            continue

        result = cv2.matchTemplate(scr_edges, _to_edges(tmpl_gray), cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        print(f"  [template] {tw}x{th} px → confidence: {max_val:.3f}")

        if max_val > best_val:
            best_val, best_loc, best_w, best_h = max_val, max_loc, tw, th

    if best_val < threshold or best_loc is None:
        print(f"  [template] no match (best: {best_val:.3f}, threshold: {threshold})")
        return None

    cx = best_loc[0] + best_w // 2
    cy = best_loc[1] + best_h // 2
    print(f"  [template] matched at ({cx}, {cy}) — confidence: {best_val:.3f}")

    out_dir = os.path.join(os.path.dirname(__file__), "..", "screenshoots", "templateMatching")
    _save_debug_image(screenshot, cx, cy, out_dir)
    return (cx, cy)


# ---------------------------------------------------------------------------
# Method 3 — Two-gate template matching
# ---------------------------------------------------------------------------

def find_icon_twoGates(
    template_raw: np.ndarray,
    screenshot: Image.Image,
) -> tuple[int, int] | None:
    """Locate the icon via two-gate multi-scale edge matching (Method 3).

    Gate 1: coarse multi-scale TM_CCOEFF_NORMED on hard Canny edges.
    Gate 2: edge hit-rate verification on soft edges inside the alpha mask.

    Args:
        template_raw: BGR or BGRA template (numpy array from cv2.imread).
        screenshot:   PIL Image of the current desktop.

    Returns:
        (cx, cy) centre of the icon, or None if not found.
    """
    GATE1_THRESH = 0.20
    GATE2_THRESH = 0.35

    # ── Prepare template ─────────────────────────────────────────────────
    if template_raw.shape[2] == 4:
        alpha = template_raw[:, :, 3]
        icon_mask = cv2.erode((alpha > 0).astype(np.uint8) * 255, _ERODE_KERNEL)
        tmpl_gray = cv2.cvtColor(_composite_on_grey(template_raw), cv2.COLOR_BGR2GRAY)
    else:
        icon_mask = None
        tmpl_gray = cv2.cvtColor(template_raw, cv2.COLOR_BGR2GRAY)

    tmpl_edges_hard = cv2.Canny(tmpl_gray, 50, 200)
    tmpl_edges_soft = _soft_edges(tmpl_gray, mask=icon_mask)
    tH, tW = tmpl_edges_hard.shape

    scr_gray = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2GRAY)

    # ── Gate 1: multi-scale coarse search ────────────────────────────────
    best = None  # (score, location, scale_ratio)
    for scale in np.linspace(0.2, 2.0, 20)[::-1]:
        new_w = int(scr_gray.shape[1] * scale)
        new_h = int(scr_gray.shape[0] * scale)
        resized = cv2.resize(scr_gray, (new_w, new_h))
        if resized.shape[0] < tH or resized.shape[1] < tW:
            break
        ratio = scr_gray.shape[1] / float(resized.shape[1])
        result = cv2.matchTemplate(cv2.Canny(resized, 50, 200), tmpl_edges_hard, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if best is None or max_val > best[0]:
            best = (max_val, max_loc, ratio)

    if best is None:
        print("  [template] no valid scales found — screenshot too small for template")
        return None

    score, loc, r = best
    print(f"Gate 1 (primary edge score): {score:.3f}")
    if score < GATE1_THRESH:
        print("Icon NOT found (gate 1 failed)")
        return None

    startX, startY = int(loc[0] * r), int(loc[1] * r)
    endX, endY = int((loc[0] + tW) * r), int((loc[1] + tH) * r)

    # ── Gate 2: edge hit-rate on candidate region ────────────────────────
    candidate = cv2.resize(scr_gray[startY:endY, startX:endX], (tW, tH))
    hit_rate = _edge_hit_rate(tmpl_edges_soft, _soft_edges(candidate), mask=icon_mask)
    print(f"Gate 2 (edge hit-rate):      {hit_rate:.3f}")

    if hit_rate < GATE2_THRESH:
        print("Icon NOT found (gate 2 failed)")
        return None

    cx, cy = (startX + endX) // 2, (startY + endY) // 2
    print(f"Icon FOUND at center ({cx}, {cy})")

    out_dir = os.path.join(os.path.dirname(__file__), "..", "screenshoots", "twoGates")
    _save_debug_image(screenshot, cx, cy, out_dir)
    return (cx, cy)


# ---------------------------------------------------------------------------
# Win32 popup dismissal
# ---------------------------------------------------------------------------

def dismiss_popup_win32() -> bool:
    """Dismiss an unexpected popup by clicking its first dismiss button via Win32.

    Checks if the foreground window is not Notepad — if so, enumerates child
    buttons and clicks the first one matching ok → yes → close → cancel.

    Returns True if a popup was found and dismissed.
    """
    t0 = time.perf_counter()

    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return False

    title = win32gui.GetWindowText(hwnd)
    if not title or "Notepad" in title or hwnd == win32gui.GetDesktopWindow():
        return False

    print(f"  [popup-win32] detected '{title}' in {(time.perf_counter() - t0) * 1000:.1f} ms")

    # Enumerate child buttons
    buttons: list[tuple[int, str]] = []

    def _enum(child_hwnd, _):
        if win32gui.GetClassName(child_hwnd) == "Button":
            buttons.append((child_hwnd, win32gui.GetWindowText(child_hwnd).strip().lower()))

    win32gui.EnumChildWindows(hwnd, _enum, None)
    t_enum = time.perf_counter()
    print(f"  [popup-win32] enumerated {len(buttons)} button(s) in {(t_enum - t0) * 1000:.1f} ms")

    # Try preferred labels first, then fall back to the first button
    for label in ("ok", "yes", "close", "cancel"):
        for btn_hwnd, btn_text in buttons:
            if btn_text == label:
                win32gui.PostMessage(btn_hwnd, win32con.BM_CLICK, 0, 0)
                print(f"  [popup-win32] clicked '{btn_text}' — total: {(time.perf_counter() - t0) * 1000:.1f} ms")
                return True

    if buttons:
        btn_hwnd, btn_text = buttons[0]
        win32gui.PostMessage(btn_hwnd, win32con.BM_CLICK, 0, 0)
        print(f"  [popup-win32] clicked '{btn_text}' (fallback) — total: {(time.perf_counter() - t0) * 1000:.1f} ms")
        return True

    print("  [popup-win32] no buttons found in popup window")
    return False
