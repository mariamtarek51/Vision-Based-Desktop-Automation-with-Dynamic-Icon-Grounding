"""
Vision-based desktop automation — entry point.

Workflow per post:
  1. Minimise all windows to expose the desktop
  2. Take a fresh screenshot
  3. Gemini grounding → locate Notepad icon (1 API call)
  4. Double-click → launch Notepad
  5. Paste post content via clipboard
  6. Ctrl+S → Save As → type full path → Enter
  7. Alt+F4 → close Notepad

Set the API key before running:
  $env:GEMINI_API_KEY="your_key"   (PowerShell)
  set GEMINI_API_KEY=your_key      (cmd)
"""

import os
import sys
import time

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


def ensure_project_dir(path: str) -> str:
    project_dir = os.path.join(path, "tjm-project")
    os.makedirs(project_dir, exist_ok=True)
    print(f"[setup] output directory: {project_dir}")
    return project_dir


def find_notepad_icon(retries: int = 3) -> tuple[int, int] | None:
    """Minimise windows, screenshot, ground icon. Retries up to `retries` times."""
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


def process_post(post: dict, project_dir: str, coord: tuple[int, int]) -> bool:
    post_id = post["id"]
    filepath = os.path.join(project_dir, f"post_{post_id}.txt")

    print(f"\n{'='*60}")
    print(f"[post {post_id:02d}] {post['title'][:60]}")
    print(f"{'='*60}")

    # ── Launch ───────────────────────────────────────────────────────────────
    minimize_all_windows()
    launched = launch_notepad(*coord)
    if not launched:
        screenshot = take_screenshot()
        if handle_popup_if_present(screenshot):
            launched = wait_for_notepad(timeout=5)
    if not launched:
        print(f"[post {post_id}] FAILED: Notepad did not open")
        return False

    # ── Type ─────────────────────────────────────────────────────────────────
    type_post_content(post["title"], post["body"])

    # ── Save (with popup recovery) ────────────────────────────────────────────
    save_as(filepath)
    if not os.path.exists(filepath):
        # File wasn't saved — a popup likely stole focus during Save As
        print(f"[post {post_id}] save failed — checking for popup…")
        if handle_popup_if_present(take_screenshot()):
            time.sleep(0.5)
            save_as(filepath)  # retry save after dismissing popup

    if not os.path.exists(filepath):
        print(f"[post {post_id}] FAILED: file not saved")
        close_notepad()
        return False

    print(f"[post {post_id}] saved → {filepath}")

    # ── Close ─────────────────────────────────────────────────────────────────
    close_notepad()

    print(f"[post {post_id}] done ✓")
    return True


def main() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY is not set.")
        print("Set it with:  $env:GEMINI_API_KEY='your_key'")
        sys.exit(1)

    project_dir = ensure_project_dir(os.path.join(os.path.dirname(__file__), "automated"))

    print("\n[setup] fetching posts…")
    posts = fetch_posts(limit=10)
    print(f"[setup] {len(posts)} posts ready\n")

    # Ground the icon once — reuse coordinates for all posts
    print("[setup] locating Notepad icon…")
    coord = find_notepad_icon(retries=3)
    
    #coord=(1851,896)
    if coord is None:
        print("ERROR: could not locate Notepad icon. Aborting.")
        sys.exit(1)
    print(f"[setup] Notepad icon at {coord} — will reuse for all posts\n")

    ok, fail = 0, 0
    for post in posts:
        if process_post(post, project_dir, coord):
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
