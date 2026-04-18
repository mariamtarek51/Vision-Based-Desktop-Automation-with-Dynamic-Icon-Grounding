"""
Vision-based desktop automation — entry point.

Three grounding methods available at startup:
  1. Gemini vision         — AI detection, requires GEMINI_API_KEY, uses API quota.
  2. Multi-template        — offline OpenCV matching against notepad_icon_*.png.
  3. Two-gate template     — offline multi-scale OpenCV with edge hit-rate verification.

Set the API key before running (method 1 only):
  $env:GEMINI_API_KEY="your_key"   (PowerShell)
  set GEMINI_API_KEY=your_key      (cmd)
"""

import os
import sys
import time

import cv2

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
)
from src.grounding import ground_icon
from src.template_grounding import (
    dismiss_popup_win32,
    find_icon_twoGates,
    ground_icon_template,
    load_all_templates,
)

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")


def ensure_project_dir(path: str) -> str:
    project_dir = os.path.join(path, "tjm-project")
    os.makedirs(project_dir, exist_ok=True)
    print(f"[setup] output directory: {project_dir}")
    return project_dir


def _ground_once(method: int, templates: list | None, template_raw) -> tuple[int, int] | None:
    """Single grounding attempt — called inside the per-post attempt loop."""
    minimize_all_windows()
    screenshot = take_screenshot()
    if method == 1:
        return ground_icon(NOTEPAD_TARGET, screenshot, retries=1)
    if method == 2:
        return ground_icon_template(templates, screenshot)
    return find_icon_twoGates(template_raw, screenshot)


def _dismiss_popup(method: int) -> bool:
    """Route popup dismissal to the correct handler based on active method."""
    if method == 1:
        return handle_popup_if_present(take_screenshot())
    return dismiss_popup_win32()


def _save_succeeded(filepath: str, since: float) -> bool:
    """Return True only if the file exists AND was written after `since`."""
    return os.path.exists(filepath) and os.path.getmtime(filepath) >= since


def process_post(
    post: dict,
    project_dir: str,
    method: int,
    coord: tuple[int, int] | None = None,
    templates: list | None = None,
    template_raw=None,
) -> tuple[bool, tuple[int, int] | None]:
    post_id = post["id"]
    filepath = os.path.join(project_dir, f"post_{post_id}.txt")

    print(f"\n{'='*60}")
    print(f"[post {post_id:02d}] {post['title'][:60]}")
    print(f"{'='*60}")
    minimize_all_windows()

    # Methods 2 & 3 always re-detect per post; only method 1 reuses across posts.
    if method in (2, 3):
        coord = None

    # ── Unified attempt loop: ground → launch → type → save ─────────────────
    # Each attempt runs the full pipeline. Inner fallbacks:
    #   • launch failure → popup dismiss + one reclick
    #   • save failure   → popup dismiss + one retry save
    # Only after BOTH inner fallbacks fail does the attempt itself fail.
    # On launch failure → coord is nulled (icon location suspect → re-ground next time).
    # On save failure after successful launch → coord is kept (click worked).
    # Any failed attempt that got past launch closes Notepad so the next launch starts clean.
    MAX_ATTEMPTS = 3
    succeeded = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n[post {post_id}] attempt {attempt}/{MAX_ATTEMPTS}")

        # Ground if needed
        if coord is None:
            coord = _ground_once(method, templates, template_raw)
            if coord is None:
                print(f"  [attempt {attempt}] grounding failed")
                continue
            print(f"  [attempt {attempt}] icon at {coord}")

        # Launch (with popup + reclick fallback)
        if not launch_notepad(*coord):
            print(f"  [attempt {attempt}] launch failed — checking for popup")
            if not (_dismiss_popup(method) and launch_notepad(*coord)):
                coord = None
                continue

        # Type
        type_post_content(post["title"], post["body"])

        # Save (with popup + retry-save fallback)
        t_before_save = time.time()
        save_as(filepath)
        if not _save_succeeded(filepath, t_before_save):
            print(f"  [attempt {attempt}] save failed — checking for popup")
            if _dismiss_popup(method):
                t_before_save = time.time()
                save_as(filepath)

        if _save_succeeded(filepath, t_before_save):
            succeeded = True
            break

        print(f"  [attempt {attempt}] save failed — will retry from scratch")
        close_notepad()

    if not succeeded:
        print(f"[post {post_id}] FAILED after {MAX_ATTEMPTS} attempts")
        close_notepad()
        return False, coord

    print(f"[post {post_id}] saved → {filepath}")
    close_notepad()
    print(f"[post {post_id}] done ✓")
    return True, coord


def main() -> None:
    print("Select grounding method:")
    print("  1. Gemini vision         (requires GEMINI_API_KEY)")
    print("  2. Multi-template        (needs notepad_icon_*.png per icon size)")
    print("  3. Two-gate template     (needs one notepad.png, handles size variation)")

    try:
        method = int(input("Enter 1, 2, or 3: ").strip())
    except ValueError:
        print("ERROR: please enter a number (1, 2, or 3).")
        sys.exit(1)
    if method not in (1, 2, 3):
        print("ERROR: invalid choice. Enter 1, 2, or 3.")
        sys.exit(1)
    if method == 1 and not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY is not set.")
        print("Set it with:  $env:GEMINI_API_KEY='your_key'")
        sys.exit(1)

    t_start = time.time()

    project_dir = ensure_project_dir(os.path.join(os.path.dirname(__file__), "automated"))

    print("\n[setup] fetching posts…")
    posts = fetch_posts(limit=10)
    print(f"[setup] {len(posts)} posts ready\n")

    # ── Grounding setup ──────────────────────────────────────────────────────
    # Grounding now happens lazily inside process_post's attempt loop.
    # For method 1, a successful coord is cached across posts to save API calls.
    coord = None
    templates = None
    template_raw = None

    if method == 2:
        try:
            templates = load_all_templates(ASSETS_DIR)
        except ValueError as exc:
            print(f"ERROR: {exc}")
            sys.exit(1)
        print("[setup] templates loaded — will re-detect icon for each post\n")
    elif method == 3:
        notepad_path = os.path.join(ASSETS_DIR, "notepad.png")
        template_raw = cv2.imread(notepad_path, cv2.IMREAD_UNCHANGED)
        if template_raw is None:
            print(f"ERROR: could not load {notepad_path}")
            sys.exit(1)
        print("[setup] template loaded — will re-detect icon for each post\n")

    # ── Process posts ─────────────────────────────────────────────────────────
    ok, fail = 0, 0
    for post in posts:
        success, coord = process_post(post, project_dir, method=method, coord=coord,
                                      templates=templates, template_raw=template_raw)
        if success:
            ok += 1
        else:
            fail += 1
        time.sleep(1)

    elapsed = time.time() - t_start
    print(f"\n{'='*60}")
    print(f"[done] {ok} succeeded, {fail} failed")
    print(f"[done] total time: {elapsed:.1f}s")
    print(f"[done] files in: {project_dir}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
