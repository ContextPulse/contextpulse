# Lessons Learned — ContextPulse

## Format
```
### [Date] Short title
**Context:** What happened?
**Lesson:** What to do going forward?
```

---

### [2026-03-15] Cloudflare Account API tokens vs User API tokens
**Context:** Created a Cloudflare API token from the Account-level page, but Registrar API calls returned unauthorized. Needed a User-level token from My Profile → API Tokens instead.
**Lesson:** Cloudflare has two separate token systems. Account tokens (dash.cloudflare.com/ACCT_ID/profile/api-tokens) scope to one account. User tokens (dash.cloudflare.com/profile/api-tokens) scope to the user across all accounts. Registrar/Domains endpoints require User tokens.

### [2026-03-15] Cloudflare domain registration is dashboard-only (non-Enterprise)
**Context:** Tried to register domains via Cloudflare API. The Registrar API only exposes read endpoints for non-Enterprise plans — no programmatic registration.
**Lesson:** Register domains through the Cloudflare dashboard manually. API is useful for DNS management and domain info queries, not for purchasing.

### [2026-03-15] Google Cloud Domains does not support .ai TLD
**Context:** Tried `gcloud domains registrations get-register-parameters contextpulse.ai` — returned `availability: UNSUPPORTED`.
**Lesson:** Google Cloud Domains has limited TLD support. For .ai, .io, and other exotic TLDs, use Cloudflare or a traditional registrar.

### [2026-03-15] SynapseAI has Intel/Habana Labs trademark conflict
**Context:** Researched domain availability for SynapseAI. All domains taken, and Intel's Habana Labs has a product called "SynapseAI" (their AI software suite for Gaudi accelerators).
**Lesson:** Always check trademark conflicts before investing in a product name. Intel's SynapseAI is well-established — rename the project.

### [2026-03-15] Private Python functions exposed via public API must be renamed
**Context:** `mcp_server.py` called `capture._find_monitor_at_cursor()` and `capture._mss_to_pil()` — underscore-prefixed functions that are private by convention.
**Lesson:** If an internal function needs to be called from another module, drop the underscore prefix to make it part of the public API. Private functions should only be used within their own module.

### [2026-03-15] numpy bool comparison: use truthiness, not `is True`
**Context:** Tests like `assert buf._has_changed(arr) is True` failed because numpy returns `np.True_` (a numpy bool), not Python's `True`. `np.True_ is True` evaluates to `False`.
**Lesson:** Never use `is True`/`is False` with values that might be numpy bools. Use `assert expr` or `assert not expr` instead, which works with any truthy/falsy value.


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

### [2026-03-15] Win32 SendInput doesn't reliably reach pynput's keyboard hook cross-process
**Context:** Tried to test hotkeys by injecting Ctrl+Shift+S via `SendInput` from the test process to a background daemon using pynput's `WH_KEYBOARD_LL` hook. The keys never reached the daemon's listener.
**Lesson:** Cross-process keyboard injection via SendInput is unreliable for testing pynput hooks. Instead, test hotkey handler logic in-process by directly calling app methods and simulating the `_pressed_keys` set. Reserve SendInput for UI automation, not unit/acceptance testing.
