# Vision-Based Desktop Automation

Fetches 10 blog posts from a public API and saves each one as a `.txt` file by automating
Windows Notepad end-to-end: locates the Notepad icon via computer vision, double-clicks it,
pastes the content, and saves through the Save As dialog.

---

## Quick Start

```powershell
# 1. Install dependencies (uv reads pyproject.toml + uv.lock)
uv sync

# 2. Run — you'll be prompted to pick a detection method (1, 2, or 3)

# Method 1 — Gemini vision (requires API key)
$env:GEMINI_API_KEY="your_key_here"
uv run python main.py

# Method 2 — Multi-template (offline; needs assets/notepad_icon_*.png per icon size)
uv run python main.py

# Method 3 — Two-gate template (offline; needs only assets/notepad.png)
uv run python main.py
```

Output files land in `automated/tjm-project/post_1.txt … post_10.txt`.

> **Note:** if `jsonplaceholder.typicode.com` is blocked on your network, connect through
> Cloudflare WARP (free VPN) before running. Otherwise the app falls back to 10 built-in
> dummy posts.

---

## Project Structure

```
D:\AutomationTask\
├── main.py                         # Entry point — orchestrates the full flow
├── assets/                         # Icon reference images (PNG)
├── src/
│   ├── api_client.py               # Fetches posts from JSONPlaceholder
│   ├── grounding.py                # Method 1: Gemini vision detection
│   ├── template_grounding.py       # Methods 2 & 3: OpenCV matching + Win32 popup dismissal
│   └── automation.py               # Notepad control: launch, type, save, close
├── test/                           # Standalone test scripts
├── automated/tjm-project/          # Output .txt files
└── screenshoots/                   # Debug images (gemini/, templateMatching/, twoGates/)
```

---

## The Three Detection Methods

| | Method 1 (Gemini) | Method 2 (Multi-template) | Method 3 (Two-gate) |
|---|---|---|---|
| API key needed | Yes | No | No |
| Reference images | None | One per icon size | One (any size) |
| Handles icon size variation | Yes | No | Yes (multi-scale) |
| Speed per detection | 1–3 s | < 100 ms | < 200 ms |
| API cost | Yes (quota) | Free | Free |

**Method 1 — Gemini Vision:** sends a screenshot to the API and asks for a bounding box
around the Notepad icon. Converts the normalised 0–1000 coords to pixels.

**Method 2 — Multi-template matching:** runs `cv2.matchTemplate` (TM_CCOEFF_NORMED) on
Canny edges between each pre-captured reference image and the screenshot. Edge-based
matching makes it wallpaper-agnostic. Needs one reference per icon size because Windows
renders different artwork at each size class.

**Method 3 — Two-gate template matching:** uses a single reference image with two
verification stages. Gate 1 does a 20-scale coarse edge search (threshold 0.20). Gate 2
verifies by edge hit-rate inside the alpha mask (threshold 0.35) — a plain ratio that
cross-correlation can't inflate on sparse images.

---

## Per-Post Retry Flow

Each post runs inside a unified 3-attempt loop. Every attempt executes the full pipeline,
with inner one-shot popup recovery at the two steps most likely to be blocked.

```
for attempt in 1..3:
    ├─ Ground   (skipped if a cached coord is still valid)
    │    └─ fails → next attempt
    │
    ├─ Launch (double-click coord)
    │    └─ fails → dismiss popup + reclick
    │               └─ still fails → null coord, next attempt (re-ground)
    │
    ├─ Type     (paste title + body via clipboard)
    │
    └─ Save     (Ctrl+S → paste path → Enter)
         └─ save unverified → dismiss popup + retry save
                              └─ still fails → close Notepad, next attempt

all 3 attempts fail → mark post FAILED, continue to the next post
```

**Coord caching by method:**
- **Method 1:** lazy grounding on the first post; coord cached across posts to save API
  calls. Re-grounds only when a launch failure invalidates the cache.
- **Methods 2 & 3:** re-ground at the start of every post — they're free and fast.

---

## Popup Handling

Popups can appear during launch or during save. Dismissal routes by method:

- **Method 1 — Gemini:** a guard first checks if the foreground is a `#32770` dialog
  **owned by Notepad** (e.g. the legitimate Save As window) — if so, the handler exits
  without calling Gemini. Otherwise it asks Gemini to find a dismiss button (OK/Close/
  Cancel/Yes/No) and clicks it.
- **Methods 2 & 3 — Win32 API:** checks the foreground window class, enumerates child
  `Button` controls, and clicks the first match in `OK → Yes → Close → Cancel → fallback`.
  Fully offline, < 2 ms.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `google-genai` | Gemini API client (Method 1) |
| `opencv-python` | Template matching + Canny edges (Methods 2 & 3) |
| `pyautogui` | Mouse clicks, keyboard input, screenshots |
| `pywin32` | Win32 API: windowing, clipboard, popup dismissal |
| `numpy` | Image array ops |
| `Pillow` | Image handling + JPEG encoding |
| `requests` | HTTP client for fetching posts |

---

## Deep Comparison: The Three Approaches

### Method 1 — Gemini Vision: The Idea

The core idea is to **offload perception entirely to a multimodal LLM**. Instead of
writing any image-processing logic, we take a screenshot, send it to Google Gemini, and
ask: "Where is the Notepad icon?" The model returns a bounding box in normalised
coordinates, which we convert to screen pixels.

**The logic:**
1. Capture a full desktop screenshot.
2. Encode it as JPEG and send it to the Gemini API with a detection prompt.
3. Parse the returned JSON bounding box `[y_min, x_min, y_max, x_max]` (0–1000 scale).
4. Convert to pixel coordinates and return the centre.

**Why this approach?**
The appeal is zero setup and maximum generality. No reference images need to be captured.
The model understands what "Notepad" looks like across any wallpaper, icon size, theme,
or screen resolution. It works even if the icon is partially occluded or in an unexpected
location.

**Pros:**
- Zero reference image setup — works immediately with just an API key.
- Handles any wallpaper, theme, icon size, or resolution without configuration.
- Can understand context (e.g., distinguishing Notepad from similar-looking icons by
  reading the label text underneath).
- Popup dismissal uses the same model, so it works for any dialog without hardcoded
  button labels.

**Cons:**
- Requires a Google Gemini API key and active internet connection.
- Free tier is limited to 20 requests/day per model — a quota-exhausted run fails entirely.
- Each detection call takes 1–3 seconds (network round-trip + model inference), far slower
  than local OpenCV matching.
- Ongoing cost if scaled beyond the free tier.

---

### Method 2 — Multi-Template Matching: The Idea

The core idea is to **compare pixel-level edge structure** between pre-captured reference
icons and the live desktop screenshot. By running Canny edge detection first, the wallpaper
is effectively erased — only the icon's outlines and internal features survive as edges.
The match is then purely about shape similarity.

**The logic:**
1. Pre-capture the Notepad icon at each desktop icon size setting (small, medium, large)
   and save them as `notepad_icon_*.png` in `assets/`.
2. At runtime, load all templates. For each one:
   - Convert both template and screenshot to grayscale.
   - Apply Canny edge detection to both.
   - Slide the template edge image over the screenshot edge image using
     `cv2.matchTemplate` with `TM_CCOEFF_NORMED`.
   - Record the best match score and location.
3. Accept the template with the highest score if it exceeds the threshold (0.5).
4. Return the centre pixel of the matched region.

**Why this approach?**
Template matching is one of the most straightforward computer vision techniques — it
literally asks "where in image A does patch B appear?" The Canny edge preprocessing is
the key insight: raw pixel matching would fail whenever the wallpaper changes, but edge
matching is background-agnostic because flat-coloured wallpaper regions produce zero edges.

The reason we need multiple templates (one per icon size) rather than one resizable template
is a Windows-specific detail: Windows doesn't scale a single icon image to different sizes.
It renders completely different artwork at 32 px, 48 px, and 96 px. A small-icon template
resized to 96 px will not match the large-icon artwork because the pixel details are
fundamentally different.

**Pros:**
- Fully offline — no API key, no internet, no ongoing cost.
- Extremely fast: each detection takes < 100 ms (pure CPU, no network).
- Deterministic: the same screenshot always produces the same result.
- High confidence threshold (0.5) means very few false positives — when it finds a match,
  it's almost certainly correct.
- Wallpaper-independent thanks to Canny edge preprocessing.

**Cons:**
- Cannot handle icon size variation at runtime — if the desktop icon size doesn't match
  any captured template exactly, the match will fail.
- Fragile across Windows versions or icon pack changes — the reference images become stale
  if the icon artwork changes.
- The transparent-background compositing (onto grey) is a workaround to prevent false
  Canny edges at the boundary; it works well but adds a subtle assumption about the icon's
  alpha channel quality.

---

### Method 3 — Two-Gate Template Matching: The Idea

The core idea is to **combine multi-scale search with a verification step** so that a
single reference image can match the icon at any size, while still rejecting false
positives. Gate 1 casts a wide net (low threshold, many scales), and Gate 2 confirms
the catch (strict edge overlap ratio).

**The logic:**
1. Load one BGRA reference image (`assets/notepad.png`).
2. Prepare the template:
   - Composite transparent pixels onto grey (128) to avoid false boundary edges.
   - Extract an eroded alpha mask — only pixels well inside the icon body count.
   - Compute "hard" Canny edges (for Gate 1) and "soft" Canny edges (for Gate 2).
3. **Gate 1 — Coarse multi-scale search:**
   - Resize the screenshot to 20 different scales (0.2x to 2.0x).
   - At each scale, run `TM_CCOEFF_NORMED` between the screenshot's Canny edges and
     the template's hard edges.
   - Keep the single best `(score, location, scale_ratio)` across all scales.
   - Reject if the best score is below 0.20 (very permissive — just filters out noise).
4. **Gate 2 — Edge hit-rate verification:**
   - Crop the candidate region identified by Gate 1 from the original-resolution
     screenshot and resize it to the template dimensions.
   - Apply soft edges (blurred + dilated Canny) to both template and candidate.
   - Compute the **edge hit-rate**: what fraction of template edge pixels (inside the
     alpha mask) overlap a candidate edge pixel?
   - Reject if below 0.35.
5. If both gates pass, return the centre of the candidate region.

**Why two gates instead of one?**
`TM_CCOEFF_NORMED` is a normalised cross-correlation. On sparse edge images (which
desktop screenshots often are), it can produce misleadingly high scores for regions that
are mostly empty — two sparse edge images can correlate well simply because they're both
mostly black. Gate 1 alone would produce too many false positives.

The edge hit-rate in Gate 2 is fundamentally different: it asks "what percentage of the
template's edge pixels actually land on a candidate edge pixel?" This is a plain ratio
that cannot be inflated by sparsity. A wrong region has its edges in different positions,
so even if Gate 1 said it looked good, Gate 2 will reject it.

The soft edges (lower Canny thresholds + dilation) in Gate 2 provide tolerance for slight
misalignment after the scale conversion — edges don't need to match pixel-for-pixel, just
be in roughly the right place.

**Why erode the alpha mask?**
The raw alpha channel includes the icon's outermost transparent pixels. After compositing
onto grey, these boundary pixels can produce weak edges that don't correspond to real icon
features. Eroding the mask by 5 px shrinks it inward, so Gate 2 only counts edges that are
clearly inside the icon body.

**Pros:**
- Only one reference image needed — no per-size captures required.
- Handles icon size variation at runtime via multi-scale search (0.2x to 2.0x range).
- Fully offline — no API key, no internet, no ongoing cost.
- Two-gate design gives strong false-positive rejection while keeping the search permissive
  enough to find the icon at unusual scales.
- The alpha mask means transparent-background PNGs work correctly without boundary artifacts.

**Cons:**
- Slower than Method 2: the multi-scale loop runs `matchTemplate` 20 times instead of once
  per template (still under 200 ms total, but measurably slower).
- Gate 1's low threshold (0.20) means the coarse search alone is unreliable — the method
  depends entirely on Gate 2 for accuracy.
- Struggles with very complex or high-frequency wallpapers where desktop edges compete with
  icon edges, producing ambiguous hit-rates.

---

### Summary: When to Use Each Method

| Scenario | Recommended Method |
|----------|--------------------|
| Quick demo, any machine, don't care about API cost | **Method 1** (Gemini) |
| Production use on a fixed machine with known icon sizes | **Method 2** (Multi-template) |
| Single setup, icon size may change, offline required | **Method 3** (Two-gate) |
| Very complex wallpaper, offline required | **Method 2** (Multi-template) |
| No setup at all, just make it work | **Method 1** (Gemini) |

**Method 1** is the most flexible but the most expensive. **Method 2** is the most reliable
but the most rigid. **Method 3** sits in the middle — more flexible than Method 2, more
reliable than Method 1 for offline use, but with a more complex failure mode when edge
detection encounters a busy desktop.
