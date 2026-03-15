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
