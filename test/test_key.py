"""Check if gemini-3-flash-preview has remaining quota."""
import os
import sys

from google import genai

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("ERROR: GEMINI_API_KEY not set.")
    sys.exit(1)

client = genai.Client(api_key=api_key)

print("Testing gemini-3-flash-preview...", end=" ", flush=True)
try:
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents="Say hello in one word.",
    )
    print(f"OK → {response.text.strip()}")
except Exception as exc:
    print(f"FAILED → {str(exc)[:200]}")
