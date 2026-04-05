# Lessons Learned — ContextPulse

## Format
```
### [Date] Short title
**Context:** What happened?
**Lesson:** What to do going forward?
```

---

<!-- Archived 2026-03-26: Duplicate of GLOBAL_LESSONS_LEARNED.md "[2026-03-25] AI-generated marketing numbers need human verification" (same lesson, same incident) -->

<!-- Archived 2026-03-30: gitleaks false-positives on SHA-256 → open-source-readiness check #25 + developing-python/ML Model Pinning section -->

<!-- Archived 2026-03-30: Lambda package/ gitignored — build artifact, not source → managing-serverless gotcha #9 -->

<!-- Archived 2026-03-30: HuggingFace model commit SHA pinning via API → developing-python/ML Model Pinning section -->

### [2026-03-28] activity.db lives in ~/screenshots/, not %APPDATA%/ContextPulse/
**Context:** `session_learner.py` defaulted to `%APPDATA%/ContextPulse/activity.db` but dev-mode daemon writes to `~/screenshots/activity.db` (controlled by `OUTPUT_DIR` env var / `ACTIVITY_DB_PATH` in `contextpulse_core.config`).
**Lesson:** Always use `ACTIVITY_DB_PATH` from `contextpulse_core.config` when reading activity.db — don't hardcode the path in voice tools. The MCP server gets this right; `session_learner.py` needs the same fix before shipping.

<!-- Archived 2026-04-03: New MCP tools need server restart → incorporated into managing-mcp-servers skill ("New tools not appearing after adding to an MCP server" troubleshooting section). -->

### [2026-03-25] Waitlist forms need a real backend before launch — localStorage is a placeholder
**Context:** Added a waitlist email form to the Pro pricing card but it only saves to localStorage. This captures zero leads if the user clears their browser.
**Lesson:** Before deploying any landing page with an email capture, wire it to a real backend (Cloudflare Workers KV, DynamoDB, or a form service like Formspree). localStorage is for dev only.

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


<!-- Archived 2026-04-05: PEP 639 license/classifier conflict → developing-python skill (pyproject.toml packaging gotchas section). -->

<!-- Archived 2026-04-01: Duplicate of GLOBAL "[2026-03-21] Auto-restart loops need duplicate instance guards" — same principle, Windows mutex variant. Global lesson covers the pattern. -->

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

<!-- Archived 2026-04-05: FastMCP @tool() decorator test isolation → managing-mcp-servers skill (Troubleshooting: "Testing FastMCP / MCP tool logic" section). -->

<!-- Archived 2026-04-01: Captured in feedback_automate_testing.md memory (applied every session: "Never ask user to manually cycle through apps — automate test sequences with scripts"). -->

<!-- Archived to skills: pytest cross-package name collision → developing-python/references/project-setup.md -->

### [2026-03-21] Local screenshot storage — real security threat is machine compromise, not the screenshots
**Context:** Debated whether to redact secrets from OCR text since ContextPulse captures everything on screen. Realized that if an attacker has access to the screenshots folder, they already have access to the `.env` files, `~/.aws/`, browser passwords, etc.
**Lesson:** OCR redaction is defense-in-depth against *accidental exposure* (sharing, cloud sync, backup). It's not the primary security boundary. Make it opt-in/out so power users who want to search for secrets they had on screen can do so.

<!-- Archived to skills: return type ripple → developing-python/references/windows-gotchas.md -->

<!-- Archived 2026-03-22: Duplicate of GLOBAL_LESSONS_LEARNED.md "[2026-03-21] Google AI Studio vs Cloud Console have separate billing" -->

<!-- Archived 2026-03-22: Duplicate of GLOBAL_LESSONS_LEARNED.md "[2026-03-21] One session per agent prompt — never combine two plans" -->

<!-- Archived 2026-03-24: Incorporated into designing-logos skill (structural edit limitation section) -->

<!-- Archived to skills: SQLite migrations → developing-python/references/windows-gotchas.md -->

