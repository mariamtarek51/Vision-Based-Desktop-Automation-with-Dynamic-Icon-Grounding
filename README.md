# Vision-Based Desktop Automation

A Windows desktop automation tool that fetches blog posts from a public API and saves each one as a `.txt` file — entirely through automated Notepad interaction. The app locates the Notepad icon on the desktop using computer vision, double-clicks it, pastes the post content, saves the file via the Save As dialog, and closes Notepad. It repeats this for all 10 posts automatically.

---

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│                        main.py                          │
│                                                         │
│  1. Ask user: Gemini vision or Template matching?       │
│  2. Fetch 10 posts from JSONPlaceholder API             │
│  3. For each post:                                      │
│       ├─ Locate Notepad icon on desktop                 │
│       ├─ Double-click to launch Notepad                 │
│       ├─ Paste post content via clipboard               │
│       ├─ Save as post_N.txt via Save As dialog          │
│       └─ Force-close Notepad                            │
└─────────────────────────────────────────────────────────┘
```

The app supports two icon detection strategies — an AI-powered approach using Google Gemini, and a fully offline approach using OpenCV template matching.

---

## Project Structure

```
D:\AutomationTask\
├── main.py                         # Entry point — orchestrates the full flow
├── assets/
│   ├── notepad_icon_small.png      # Template for small desktop icon size
│   ├── notepad_icon_medium.png     # Template for medium desktop icon size
│   └── notepad_icon_large.png      # Template for large desktop icon size
├── src/
│   ├── api_client.py               # Fetches posts from JSONPlaceholder API
│   ├── grounding.py                # Method 1: Gemini vision detection pipeline
│   ├── template_grounding.py       # Method 2: OpenCV edge template matching + Win32 popup dismissal
│   └── automation.py               # Notepad control: launch, type, save, close
├── test/
│   ├── test_template_grounding.py  # Runs template matching and saves debug images
│   ├── open_popup.py               # Spawns a real Windows dialog for popup testing
│   ├── test_grounding.py           # Runs Gemini grounding and saves annotated images
│   ├── test_automation.py          # Tests Notepad automation with manual coordinates
│   └── test_key.py                 # Checks remaining Gemini API quota
└── automated/tjm-project/          # Output: post_1.txt … post_10.txt
```

---

## Requirements

- Windows 10 or 11
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Notepad shortcut visible on the desktop
- **Method 1 only:** Google Gemini API key (free tier available)
- **Method 2 only:** Pre-saved template images in `assets/`

---

## Installation

```powershell
uv sync
```

---

## Internet & VPN Notice

> **The app fetches posts from `jsonplaceholder.typicode.com`.**
> This API may be blocked or unreachable on some networks.
> If you cannot access it directly, connect through **Cloudflare WARP** (free VPN) before running.
> Without a working connection the app will automatically fall back to 10 built-in dummy posts
> and continue running — no crash, but the content will not be real API data.

Download Cloudflare WARP: https://one.one.one.one/

---

## Running the App

### Method 1 — Gemini Vision

Requires a Google Gemini API key. The icon is located once before the loop and the coordinates are reused for all 10 posts.

```powershell
$env:GEMINI_API_KEY="your_key_here"
uv run python main.py
# When prompted: enter 1
```

### Method 2 — Template Matching (Offline)

No API key needed. Requires pre-saved icon images in the `assets/` folder.

```powershell
uv run python main.py
# When prompted: enter 2
```

---

## Output

All files are saved to `automated/tjm-project/`:

```
automated/tjm-project/
├── post_1.txt
├── post_2.txt
├── ...
└── post_10.txt
```

Each file contains:
```
Title: <post title>

<post body>
```

---

## Popup Handling

Unexpected popups (security warnings, "Replace file?" dialogs, system alerts) can interrupt
the automation at two points: during Notepad launch or during Save As. The app detects and
dismisses them automatically, then retries the interrupted step.

### Save Recovery — How the App Knows a Popup Blocked the Save

After calling Save As, the app does not assume the file was saved. It checks whether the file
actually exists on disk using `os.path.exists(filepath)`. If the file is missing, it means
something went wrong — most likely a popup appeared on top of the Save As dialog and stole
focus, preventing the filename from being typed or confirmed.

```
save_as(filepath)
       │
       ▼
os.path.exists(filepath)?
       │
      NO → a popup probably blocked the save
       │
       ▼
dismiss_popup()  ←── Method 1: Gemini finds the button
       │              Method 2: Win32 enumerates buttons
       │
  dismissed?
       │
      YES → wait 0.5s → save_as(filepath) retry
       │
       ▼
os.path.exists(filepath)?
       │
      NO → mark post as FAILED, close Notepad, move to next post
      YES → continue normally
```

The file existence check is the source of truth — it does not matter what the UI appeared
to do. If the file is on disk, the save succeeded. If it is not, the save failed regardless
of what was shown on screen.

### Method 1 — Gemini Vision Popup Handling

Takes a fresh screenshot and sends it to the Gemini API with a description asking for a
dismiss button — labelled OK, Close, Cancel, Yes, No, or similar. If Gemini returns
coordinates for a button, the app clicks it and retries the interrupted step.

```
Popup appears
     │
     ▼
Take screenshot → Send to Gemini → "Find a dismiss button"
     │
     ▼
Gemini returns (x, y) → Click → Retry step
```

**Advantage:** works for any popup without knowing its content — Gemini reads the screen.  
**Cost:** 1 extra API call per popup encounter.

---

### Method 2 — Win32 API Popup Handling

Uses the Windows API directly — no screenshot, no vision model. Checks the foreground window,
enumerates all its buttons, and clicks the first one matching a priority list.

```
Popup appears
     │
     ▼
GetForegroundWindow() → title is not "Notepad"?
     │
     ▼
EnumChildWindows() → collect all Button controls
     │
     ▼
Click first match: OK → Yes → Close → Cancel → (any button)
```

**Advantage:** extremely fast (< 2 ms total), fully offline, no API cost.  


---

## Testing

```powershell
# Test template matching — inspect what the matcher sees
uv run python test/test_template_grounding.py
# Produces: debug_screenshot_edges.png, debug_match_result.png

# Test popup dismissal (Method 2)
# Terminal 1: spawn a test popup
uv run python test/open_popup.py
# Terminal 2: run the dismissal handler against it

# Test Gemini grounding — saves annotated detection images to grounding/
$env:GEMINI_API_KEY="your_key_here"
uv run python test/test_grounding.py

# Test full Notepad automation with a known icon position (no grounding)
uv run python test/test_automation.py <x> <y>

# Check remaining Gemini API quota
uv run python test/test_key.py
```

---

## Grounding Methods — Pros & Cons

### Method 1 — Gemini Vision

Sends a screenshot to the Google Gemini multimodal API. Gemini returns bounding box
coordinates of the target icon. A second API call verifies the detection before acting on it.

| | |
|---|---|
| **Pros** | Works with zero setup — no reference images to capture |
| | Handles any icon appearance, size, or theme automatically |
| | Can detect and dismiss any unknown popup without knowing its content |
| | Self-correcting — failed detections are masked and retried with a different prompt |
| **Cons** | Requires a Gemini API key |
| | Consumes API quota (2 calls per detection attempt) |
| | Free tier limit: 20 requests/day — enough for ~10 posts with some retries |
| | Adds latency — each API round trip takes 1–3 seconds |
| | Requires an internet connection |
| | Non-deterministic — results can vary between runs |

---

### Method 2 — Template Matching

Uses OpenCV Canny edge detection and `matchTemplate` to locate the icon on the live desktop
by comparing pre-saved reference images against the current screenshot.

| | |
|---|---|
| **Pros** | Fully offline — no API key, no internet, no quota |
| | Fast — detection runs in milliseconds |
| | Deterministic — same input always produces the same result |
| | Wallpaper-agnostic — Canny edges strip the background, only the icon shape is matched |
| | Works at any desktop position — searches the entire screenshot each time |
| **Cons** | Requires pre-saved icon images in `assets/` |
| | Breaks if the icon's visual design changes (e.g. a Windows update changes the artwork) |
| | Cannot understand popup content — dismisses by button label only (`OK → Yes → Close → Cancel`) |
| | Each Windows icon size class renders different artwork, so a separate template is needed per size |

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `google-genai` | Gemini API client for vision-based detection |
| `opencv-python` | Template matching and Canny edge detection |
| `pyautogui` | Mouse clicks, keyboard input, screenshots |
| `pywin32` | Win32 API for window management, clipboard, and popup dismissal |
| `numpy` | Image array operations |
| `Pillow` | Image handling and JPEG encoding |
| `requests` | HTTP client for fetching posts |
