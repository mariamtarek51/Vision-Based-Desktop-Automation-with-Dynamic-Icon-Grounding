"""
Vision-based desktop automation — entry point.

Two grounding methods available at startup:
  1. Gemini vision    — AI detection, requires GEMINI_API_KEY, uses API quota.
                        Grounds once before the loop, reuses coord for all posts.
  2. Template matching — offline OpenCV matching against notepad_icon.png.
                        No API calls. Run capture_icon.py first to create the
                        reference image from your actual desktop.

Set the API key before running (method 1 only):
  $env:GEMINI_API_KEY="your_key"   (PowerShell)
  set GEMINI_API_KEY=your_key      (cmd)
"""

import os
import sys
import time

import numpy as np

# Single source of truth for the assets directory — all asset paths derive from here.
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")

from src.api_client import fetch_posts
from src.automation import (
    NOTEPAD_TARGET,
    close_notepad,
    handle_popup_if_present,
    launch_notepad,
    minimize_all_windows,
    save_as,
    take_screenshot,
    type_post_content,
    wait_for_notepad,
)
from src.grounding import ground_icon
from src.template_grounding import dismiss_popup_win32, ground_icon_template, load_all_templates


def ensure_project_dir(path: str) -> str:
    project_dir = os.path.join(path, "tjm-project")
    os.makedirs(project_dir, exist_ok=True)
    print(f"[setup] output directory: {project_dir}")
    return project_dir


def find_notepad_icon(retries: int = 3) -> tuple[int, int] | None:
    """Minimise windows, screenshot, ground icon via Gemini. Retries up to `retries` times."""
    for attempt in range(1, retries + 1):
        print(f"\n[ground] attempt {attempt}/{retries}")
        minimize_all_windows()
        screenshot = take_screenshot()
        coord = ground_icon(NOTEPAD_TARGET, screenshot)
        if coord:
            return coord
        if attempt < retries:
            time.sleep(1)
    return None


def _dismiss_popup(method: int) -> bool:
    """Route popup dismissal to the correct handler based on active method."""
    if method == 2:
        return dismiss_popup_win32()
    return handle_popup_if_present(take_screenshot())


def _save_succeeded(filepath: str, since: float) -> bool:
    """Return True only if the file exists AND was written after `since` (time.time())."""
    return os.path.exists(filepath) and os.path.getmtime(filepath) >= since


def process_post(
    post: dict,
    project_dir: str,
    method: int,
    coord: tuple[int, int] | None = None,
    templates: list[np.ndarray] | None = None,
) -> bool:
    post_id = post["id"]
    filepath = os.path.join(project_dir, f"post_{post_id}.txt")

    print(f"\n{'='*60}")
    print(f"[post {post_id:02d}] {post['title'][:60]}")
    print(f"{'='*60}")

    # ── Ground ───────────────────────────────────────────────────────────────
    if method == 2:
        coord = None
        for attempt in range(1, 4):
            print(f"\n[ground] attempt {attempt}/3")
            minimize_all_windows()
            screenshot = take_screenshot()
            coord = ground_icon_template(templates, screenshot)
            if coord:
                break
            if attempt < 3:
                time.sleep(1)
        if coord is None:
            print(f"[post {post_id}] FAILED: template matching could not locate icon")
            return False
        print(f"  [template] icon at {coord}")
    else:
        minimize_all_windows()

    # ── Launch ───────────────────────────────────────────────────────────────
    launched = launch_notepad(*coord)
    if not launched:
        if _dismiss_popup(method):
            launched = launch_notepad(*coord)
    if not launched:
        print(f"[post {post_id}] FAILED: Notepad did not open")
        return False

    # ── Type ─────────────────────────────────────────────────────────────────
    type_post_content(post["title"], post["body"])

    # ── Save (with popup recovery) ────────────────────────────────────────────
    t_before_save = time.time()
    save_as(filepath)
    if not _save_succeeded(filepath, t_before_save):
        print(f"[post {post_id}] save failed — checking for popup…")
        if _dismiss_popup(method):
            time.sleep(0.5)
            t_before_save = time.time()
            save_as(filepath)

    if not _save_succeeded(filepath, t_before_save):
        print(f"[post {post_id}] FAILED: file not saved")
        close_notepad()
        return False

    print(f"[post {post_id}] saved → {filepath}")

    # ── Close ─────────────────────────────────────────────────────────────────
    close_notepad()

    print(f"[post {post_id}] done ✓")
    return True





def main() -> None:
    print("Select grounding method:")
    print("  1. Gemini vision    (requires GEMINI_API_KEY, uses API quota)")
    print("  2. Template matching (no API key needed)")
    method = int(input("Enter 1 or 2: ").strip())
    if method == 1:
        if not os.environ.get("GEMINI_API_KEY"):
            print("ERROR: GEMINI_API_KEY is not set.")
            print("Set it with:  $env:GEMINI_API_KEY='your_key'")
            sys.exit(1)
       

    
        
        

    elif method!=2 and method!=1 :
        print("ERROR: invalid choice. Enter 1 or 2.")
        sys.exit(1)

    
    

    project_dir = ensure_project_dir(os.path.join(os.path.dirname(__file__), "automated"))

    print("\n[setup] fetching posts…")
    posts = fetch_posts(limit=10)
    print(f"[setup] {len(posts)} posts ready\n")

    coord = None
    templates = None

    if method == 1:
        print("[setup] locating Notepad icon via Gemini…")
        coord = find_notepad_icon(retries=3)
        if coord is None:
            print("ERROR: could not locate Notepad icon. Aborting.")
            sys.exit(1)
        print(f"[setup] Notepad icon at {coord} — will reuse for all posts\n")

    elif method == 2:
        try:
            templates = load_all_templates(ASSETS_DIR)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            sys.exit(1)

    ok, fail = 0, 0
    for post in posts:
        if process_post(post, project_dir, method=method, coord=coord, templates=templates):
            ok += 1
        else:
            fail += 1
        time.sleep(1)

    print(f"\n{'='*60}")
    print(f"[done] {ok} succeeded, {fail} failed")
    print(f"[done] files in: {project_dir}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
