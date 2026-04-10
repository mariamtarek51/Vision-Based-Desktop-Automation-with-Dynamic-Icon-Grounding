"""
Automation test — isolated from grounding (no Gemini calls).

Runs 2 full iterations to verify the complete per-post workflow:
  open icon → paste content → save → close → reopen → paste → save → close

Usage:
    uv run python test_automation.py <x> <y>

Example:
    uv run python test_automation.py 1851 896
"""

import os
import sys

from src.automation import (
    close_notepad,
    launch_notepad,
    minimize_all_windows,
    notepad_is_open,
    save_as,
    type_post_content,
)

POSTS = [
    {"id": 1, "title": "Test Post One", "body": "Body of the first test post.\nSecond line here."},
    {"id": 2, "title": "Test Post Two", "body": "Body of the second test post.\nChecking reopen works."},
]

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "automated")


def run_post(post: dict, x: int, y: int) -> bool:
    post_id = post["id"]
    filepath = os.path.join(OUTPUT_DIR, f"test_post_{post_id}.txt")

    print(f"\n--- Post {post_id}: {post['title']} ---")

    # Open
    minimize_all_windows()
    if not launch_notepad(x, y):
        print(f"  FAILED: Notepad did not open")
        return False

    # Write
    type_post_content(post["title"], post["body"])

    # Save
    save_as(filepath)

    # Close
    close_notepad()

    if notepad_is_open():
        print(f"  WARNING: Notepad still open after close")
        return False

    # Verify file
    if not os.path.exists(filepath):
        print(f"  FAILED: file not created at {filepath}")
        return False

    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    expected_start = f"Title: {post['title']}"
    if not content.strip().startswith(expected_start):
        print(f"  FAILED: unexpected file content:\n{content[:200]}")
        return False

    print(f"  OK → {filepath}")
    print(f"  Content preview: {content[:80].strip()}")
    return True


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: uv run python test_automation.py <x> <y>")
        sys.exit(1)

    try:
        x, y = int(sys.argv[1]), int(sys.argv[2])
    except ValueError:
        print("ERROR: x and y must be integers.")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Clean up previous test files
    for post in POSTS:
        p = os.path.join(OUTPUT_DIR, f"test_post_{post['id']}.txt")
        if os.path.exists(p):
            os.remove(p)

    print(f"Icon coordinates: ({x}, {y})")
    print(f"Running {len(POSTS)} iterations...\n")

    results = [run_post(post, x, y) for post in POSTS]

    print(f"\n{'='*40}")
    passed = sum(results)
    print(f"Result: {passed}/{len(POSTS)} passed")
    if all(results):
        print("SUCCESS: full automation pipeline works correctly.")
    else:
        print("FAILED: check output above for details.")


if __name__ == "__main__":
    main()
