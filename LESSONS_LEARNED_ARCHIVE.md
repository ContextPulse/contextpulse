# LESSONS_LEARNED — Archive

### [2026-08-29] A Pro-license gate imported inside the wrapper function defeats mocking, and the test class had been silently broken as a result
**Original lesson:** **Context:** `packages/memory/tests/test_mcp.py::TestMemorySearchTool` asserted on
`memory_search`'s JSON output, which is gated behind `@_require_pro`.

**Problem:** `_require_pro`'s wrapper did `from contextpulse_core.license import
has_pro_access` *inside the function body* (lazy import). `has_pro_access()` falls
back to `not is_trial_expired()` when unlicensed, which reads real trial-state off
disk — so the whole test class silently depended on the ambient trial state of
whatever machine ran the suite instead of testing `store.hybrid_search()`/`search()`
at all. On this machine the trial reads expired, so every test failed with
`KeyError: 'count'` (the denied-tier payload has no `count` key). The error-path
payload never satisfies the assertions either way, so this class had likely never
passed anywhere — there is no green-run evidence for it in the journal.

**Fix/Pattern:** A dependency your tests need to `patch()` must be a **module-level**
import, not a function-scoped one — `unittest.mock.patch("module.name", ...)` can
only replace names that exist in the module's namespace at patch time.
`packages/screen/src/contextpulse_sight/mcp_server.py` already had this right
(`from contextpulse_core.license import get_license_tier, has_pro_access` at module
level); `packages/memory`'s copy of the same gate did not. When adding a new
Pro-gated tool, copy the screen package's import style, and give the test class an
autouse fixture that patches `has_pro_access` to `True` (plus one test asserting the
denied-path shape) — see `test_pro_tools.py` for the pattern and
`test_mcp.py::TestMemorySearchTool` for the corrected copy. See commit `275311e`.

**Archived:** 2026-09-12 (trim-lessons.py) — **VERIFIED encoded** in `developing-python` SKILL.md, marked at line 750 by its `<!-- lesson: -->` stamp.
