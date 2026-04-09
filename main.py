"""
Entry point for the vision-based desktop automation task.

Workflow (per post):
  1. Minimize all windows to expose the desktop
  2. Take a fresh screenshot
  3. Two-stage grounding → locate Notepad icon
  4. Double-click → launch Notepad
  5. Type post content via clipboard
  6. Ctrl+S → Save As → type full path → Enter
  7. Alt+F4 → close Notepad

Set the GROQ_API_KEY environment variable before running:
  set GROQ_API_KEY=<your_key>   (Windows cmd)
  $env:GROQ_API_KEY="<key>"    (PowerShell)
"""

import os
import sys
import time

from src.api_client import fetch_posts
from src.automation import (
    NOTEPAD_TARGET,
    close_notepad,
    get_desktop_path,
    handle_popup_if_present,
    launch_notepad,
    minimize_all_windows,
    notepad_is_open,
    save_as,
    take_screenshot,
    type_post_content,
    wait_for_notepad,
)
from src.grounding import ground_icon


def ensure_project_dir(desktop: str) -> str:
    """Create Desktop/tjm-project/ and return its path."""
    project_dir = os.path.join(desktop, "tjm-project")
    os.makedirs(project_dir, exist_ok=True)
    print(f"[setup] output directory: {project_dir}")
    return project_dir


def ground_notepad_icon(retries: int = 3) -> tuple[int, int] | None:
    """
    Minimize windows, take a fresh screenshot, and ground the Notepad icon.
    Retries up to `retries` times with 1-second delay.
    """
    for attempt in range(1, retries + 1):
        print(f"\n[ground] attempt {attempt}/{retries} – minimising windows…")
        minimize_all_windows()

        screenshot = take_screenshot()
        print("[ground] screenshot captured, running grounding pipeline…")

        coord = ground_icon(NOTEPAD_TARGET, screenshot, max_retries=3)
        if coord:
            return coord

        print(f"[ground] grounding returned None on attempt {attempt}")
        if attempt < retries:
            time.sleep(1)

    return None


def process_post(post: dict, project_dir: str) -> bool:
    """
    Open Notepad, type the post, save, and close.
    Returns True on success.
    """
    post_id = post["id"]
    title = post["title"]
    body = post["body"]
    filename = f"post_{post_id}.txt"
    filepath = os.path.join(project_dir, filename)

    print(f"\n{'='*60}")
    print(f"[post {post_id:02d}] {title[:60]}")
    print(f"{'='*60}")

    # ── Step 1: ground the icon ──────────────────────────────────────────────
    coord = ground_notepad_icon(retries=3)
    if coord is None:
        print(f"[post {post_id}] FAILED: could not locate Notepad icon – skipping")
        return False

    x, y = coord
    print(f"[post {post_id}] icon at ({x}, {y})")

    # ── Step 2: launch Notepad ───────────────────────────────────────────────
    launched = launch_notepad(x, y)
    if not launched:
        # Check for unexpected popup blocking launch
        screenshot = take_screenshot()
        dismissed = handle_popup_if_present(screenshot)
        if dismissed:
            print("[post] popup dismissed, retrying launch")
            launched = wait_for_notepad(timeout=5)

    if not launched:
        print(f"[post {post_id}] FAILED: Notepad did not open")
        return False

    # ── Step 3: type content ─────────────────────────────────────────────────
    type_post_content(title, body)
    print(f"[post {post_id}] content typed ({len(title) + len(body)} chars)")

    # ── Step 4: save ─────────────────────────────────────────────────────────
    save_as(filepath)
    print(f"[post {post_id}] saved → {filepath}")

    # ── Step 5: close ────────────────────────────────────────────────────────
    close_notepad()
    if notepad_is_open():
        # Last-resort: check for popup blocking close
        screenshot = take_screenshot()
        handle_popup_if_present(screenshot)
        close_notepad()

    print(f"[post {post_id}] done ✓")
    return True


def main() -> None:
    # Validate API key early
    if not os.environ.get("GROQ_API_KEY"):
        print("ERROR: GROQ_API_KEY environment variable is not set.")
        print("Set it with:  set GROQ_API_KEY=<your_key>")
        sys.exit(1)

    desktop = get_desktop_path()
    project_dir = ensure_project_dir(desktop)

    print("\n[setup] fetching posts from JSONPlaceholder…")
    posts = fetch_posts(limit=10)
    print(f"[setup] got {len(posts)} posts\n")

    results = {"ok": 0, "fail": 0}

    for post in posts:
        success = process_post(post, project_dir)
        if success:
            results["ok"] += 1
        else:
            results["fail"] += 1
        # Small gap between iterations
        time.sleep(1.0)

    print(f"\n{'='*60}")
    print(f"[done] {results['ok']} succeeded, {results['fail']} failed")
    print(f"[done] files saved to: {project_dir}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
