# Lessons Learned — ContextPulse

## Format
```
### [Date] Short title
**Context:** What happened?
**Lesson:** What to do going forward?
```

---

### [2026-03-22] Keyword routing needs stop words and raw-count scoring
**Context:** First version of contextpulse-project used sqrt-normalized scoring. Projects with tiny keyword sets (StockTrader: 8 keywords) dominated everything because any single generic match scored disproportionately high. Also, overview text extraction pulled in English stop words ("with", "your", "have") that caused false matches.
**Lesson:** (1) Use raw match count as primary sort, proportion as tiebreaker — not sqrt normalization. (2) Always filter stop words from keyword extraction. (3) Test routing against the real portfolio, not just unit test fixtures.

### [2026-03-15] Cloudflare Account API tokens vs User API tokens
**Context:** Created a Cloudflare API token from the Account-level page, but Registrar API calls returned unauthorized. Needed a User-level token from My Profile → API Tokens instead.
**Lesson:** Cloudflare has two separate token systems. Account tokens (dash.cloudflare.com/ACCT_ID/profile/api-tokens) scope to one account. User tokens (dash.cloudflare.com/profile/api-tokens) scope to the user across all accounts. Registrar/Domains endpoints require User tokens.

### [2026-03-15] Cloudflare domain registration is dashboard-only (non-Enterprise)
**Context:** Tried to register domains via Cloudflare API. The Registrar API only exposes read endpoints for non-Enterprise plans — no programmatic registration.
**Lesson:** Register domains through the Cloudflare dashboard manually. API is useful for DNS management and domain info queries, not for purchasing.

### [2026-03-15] Google Cloud Domains does not support .ai TLD
**Context:** Tried `gcloud domains registrations get-register-parameters contextpulse.ai` — returned `availability: UNSUPPORTED`.
**Lesson:** Google Cloud Domains has limited TLD support. For .ai, .io, and other exotic TLDs, use Cloudflare or a traditional registrar.

### [2026-03-15] Private Python functions exposed via public API must be renamed
**Context:** `mcp_server.py` called `capture._find_monitor_at_cursor()` and `capture._mss_to_pil()` — underscore-prefixed functions that are private by convention.
**Lesson:** If an internal function needs to be called from another module, drop the underscore prefix to make it part of the public API. Private functions should only be used within their own module.

<!-- Archived 2026-03-23: numpy bool identity check → developing-python skill (Anti-patterns) + validating-dataframes skill. Already in GLOBAL_LESSONS_LEARNED_ARCHIVE.md 2026-03-15 -->


### [2026-03-15] PEP 639 — don't mix license field with license classifiers
**Context:** `pip install -e .` failed with `InvalidConfigError: License classifiers have been superseded by license expressions` when pyproject.toml had both `license = "MIT"` and a `License :: OSI Approved :: MIT License` classifier.
**Lesson:** With PEP 639, use `license = "MIT"` (SPDX expression) and remove all `License ::` classifiers from pyproject.toml. They are mutually exclusive.

### [2026-03-15] Multiple daemon instances need single-instance guard
**Context:** Each test launch spawned a new daemon process without killing the old one, resulting in 5+ copies running simultaneously.
**Lesson:** Use a Windows named mutex (`CreateMutexW` + check `ERROR_ALREADY_EXISTS`) for single-instance enforcement. It's the standard Windows pattern — the mutex auto-releases when the process exits even if it crashes.

<!-- Archived to skills: pystray PIPE deadlock → developing-python, MCP newline JSON → managing-mcp-servers -->

### [2026-03-17] ContextPulse Sight screenshots too low-res for text recognition
**Context:** Tried to read Amazon product prices from `screen_all.png` (both monitors stitched). The image was too small to read text — product names and prices were illegible. Browser was at less than 100% zoom, and the stitched dual-monitor image compresses each monitor's detail.
**Lesson:** (1) The skill should recommend users set browser zoom to 100%+ for readable captures. (2) For text-heavy screens, prefer `get_screen_text` (OCR) over `get_screenshot` — but OCR only captures the active monitor. (3) Consider capturing each monitor separately at full resolution instead of stitching, or increase the stitched image resolution. (4) When screen content is on a non-active monitor, the current tooling has a gap — OCR only reads active, and stitched screenshots lose detail.

<!-- Archived to skills: daemon watchdog pattern → developing-python -->

### [2026-03-15] Win32 SendInput doesn't reliably reach pynput's keyboard hook cross-process
**Context:** Tried to test hotkeys by injecting Ctrl+Shift+S via `SendInput` from the test process to a background daemon using pynput's `WH_KEYBOARD_LL` hook. The keys never reached the daemon's listener.
**Lesson:** Cross-process keyboard injection via SendInput is unreliable for testing pynput hooks. Instead, test hotkey handler logic in-process by directly calling app methods and simulating the `_pressed_keys` set. Reserve SendInput for UI automation, not unit/acceptance testing.

### [2026-03-21] Stitched multi-monitor screenshots are useless for AI analysis
**Context:** `capture_all_monitors()` stitched dual 4K monitors into one image, downscaled to 2560x1440. Text was illegible, and Claude got confused by two monitors jammed together.
**Lesson:** Capture each monitor separately and return as a list of labeled images. Each stays at 1280x720 and remains readable. Buffer filenames include monitor index: `{timestamp}_m{index}.jpg`.

### [2026-03-21] Text-only storage saves 59% disk with no information loss for text-heavy screens
**Context:** Benchmarked OCR classification across 11 scenarios (Amazon, Reddit, Maps, YouTube, etc.). 45% of captures were text-heavy enough to store text-only. OCR text is ~3-5KB vs ~130KB for the image.
**Lesson:** Always store OCR text as searchable metadata alongside images. For text-heavy frames (100+ chars, 70%+ confidence), the image can be dropped. But certain apps (thinkorswim, Figma) need both image + text regardless — use app-level overrides.

### [2026-03-21] FastMCP @tool() decorator wraps functions — can't test them directly via import
**Context:** Tried `from mcp_server import get_screenshot` and calling it in tests. The `@mcp_app.tool()` decorator wraps the function, so the import returns a MagicMock, not the real function.
**Lesson:** Test MCP tool logic by testing the underlying data layer (ActivityDB, RollingBuffer) directly rather than trying to call the decorated tool functions. The MCP tools are thin wrappers over the data layer.

### [2026-03-21] Automate benchmark tests — never ask the user to cycle through apps manually
**Context:** Asked the user to manually open 8 different apps and cycle through them while a capture script ran. Had to repeat this twice due to script buffering issues. Very frustrating UX.
**Lesson:** Write automated benchmark scripts that open URLs/apps via subprocess, capture, classify, and close — zero user effort. `auto_benchmark.py` does this in 90 seconds with 11 scenarios.

<!-- Archived to skills: pytest cross-package name collision → developing-python/references/project-setup.md -->

### [2026-03-21] Local screenshot storage — real security threat is machine compromise, not the screenshots
**Context:** Debated whether to redact secrets from OCR text since ContextPulse captures everything on screen. Realized that if an attacker has access to the screenshots folder, they already have access to the `.env` files, `~/.aws/`, browser passwords, etc.
**Lesson:** OCR redaction is defense-in-depth against *accidental exposure* (sharing, cloud sync, backup). It's not the primary security boundary. Make it opt-in/out so power users who want to search for secrets they had on screen can do so.

<!-- Archived to skills: return type ripple → developing-python/references/windows-gotchas.md -->

<!-- Archived 2026-03-22: Duplicate of GLOBAL_LESSONS_LEARNED.md "[2026-03-21] Google AI Studio vs Cloud Console have separate billing" -->

<!-- Archived 2026-03-22: Duplicate of GLOBAL_LESSONS_LEARNED.md "[2026-03-21] One session per agent prompt — never combine two plans" -->

### [2026-03-21] AI logo generation — Gemini ignores "no inner ring" edits
**Context:** Tried to edit an eye logo to remove the inner iris ring via gemini_edit_image. Despite explicit prompts saying "no inner rings," Gemini kept adding them back.
**Lesson:** Gemini's edit mode preserves too much of the original image structure. For significant structural changes, generate a new image from scratch with the desired constraints rather than trying to edit away unwanted elements.

<!-- Archived to skills: SQLite migrations → developing-python/references/windows-gotchas.md -->
