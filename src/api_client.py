"""Fetch blog posts from JSONPlaceholder with graceful degradation."""

import requests

POSTS_URL = "https://jsonplaceholder.typicode.com/posts"

_FALLBACK_POSTS = [
    {
        "id": i,
        "title": f"Fallback Post {i}",
        "body": f"This is fallback content for post {i}.\nThe API was unavailable at the time of execution.",
    }
    for i in range(1, 11)
]


def fetch_posts(limit: int = 10) -> list[dict]:
    try:
        response = requests.get(POSTS_URL, params={"_limit": limit}, timeout=10)
        response.raise_for_status()
        posts = response.json()
        print(f"[api] fetched {len(posts)} posts from {POSTS_URL}")
        return posts
    except (requests.RequestException, ValueError) as exc:
        print(f"[api] request failed: {type(exc).__name__}: {exc}")
        return _FALLBACK_POSTS[:limit]
