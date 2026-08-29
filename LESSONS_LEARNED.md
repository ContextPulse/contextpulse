# Lessons Learned

Record mistakes, surprises, and patterns discovered during this project.
Be specific — vague lessons aren't actionable.

## Format
```
### [Date] Short title
**Context:** What were you trying to do?
**Problem:** What went wrong or was surprising?
**Fix/Pattern:** What is the correct approach?
```

---

### [2026-08-29] Module init before the single-instance mutex check wastes a real launch race

**Context:** The daemon's `main()` constructed `ContextPulseDaemon()` — which eagerly
runs `_init_sight()`, `_init_voice()`, `_init_touch()`, `_init_knowledge()` — before
`run()` ever checked the single-instance mutex.

**Problem:** Two overlapping launch attempts (observed at the nightly 4am restart
racing a manual relaunch) both paid the full Sight/Voice/Touch/Whisper module-init
cost (~4-7s) before the loser discovered it had already lost the race.

**Fix/Pattern:** Extract any expensive-precondition guard (mutex, lock file, port
bind) into a function callable *before* constructing the object whose construction
does the expensive work. Keep a fallback copy of the same guard inside the object's
own entry point for callers that construct-and-run it directly (tests, alternate
entry points) — don't make the fast path the *only* path, or you've just moved the
gap instead of closing it. See commit `275311e`.

### [2026-08-29] A Pro-license gate imported inside the wrapper function defeats mocking, and the test class had been silently broken as a result

**Context:** `packages/memory/tests/test_mcp.py::TestMemorySearchTool` asserted on
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

### [2026-08-27] "Unbounded scratch directory" needs the actual retention policy read before deleting anything

**Context:** `working/` was reported as 4.3GB of unbounded scratch with no retention
policy (`cp-working-dir-4gb-unbounded`).

**Problem:** The diagnosis was wrong in a way that mattered — `working/` was not
unbounded accumulation, it held exactly one real, still-referenced asset (an
interview-episode directory). A naive fix (delete anything old) would have
destroyed live data.

**Fix/Pattern:** Before writing a retention/cleanup tool against a "growing
directory" finding, enumerate what's actually in it and why each item exists. Add an
explicit protect-list (`CP_RETENTION_PROTECT` env var pattern) rather than a pure
age-based sweep for any directory that might hold intentionally-long-lived assets.

### [2026-08-24] A dependency's major-version bump can break every CI job with zero commits to this repo

**Context:** `mcp>=1.0` (unbounded) was the pin across five packages, plus a bare
`mcp[cli]` in a sixth.

**Problem:** `mcp` shipped 2.0.0, which removed `mcp.server.fastmcp`. Every CI job
started failing at collection with `ModuleNotFoundError` — with no commits to this
repo triggering it. The last green run was six weeks prior.

**Fix/Pattern:** Never leave a dependency range unbounded at the major version.
`mcp>=1.0,<2` everywhere, extras included (`mcp[cli]>=1.0,<2` — a bare `pkg[extra]`
is fully unbounded). See `developing-python`'s "Never leave a dependency range
unbounded at the major version" section for the general rule and diagnostic pattern.

### [2026-07-07] A green conformance suite on a spec-built referee is not proof of correctness

**Context:** The Phase 1 KG-spine's bi-temporal referee (`cp_core.py`) shipped with
14 conformance vectors, all green.

**Problem:** A held adversarial audit constructing inputs the vectors didn't cover
found 3 CRITICAL + 5 MAJOR bugs the green suite was structurally blind to — the
vectors were derived from the same spec that produced the code, so passing them only
proved internal consistency with that spec, not correctness against unvectored
inputs.

**Fix/Pattern:** For any spec-built pure-logic core (a referee, a risk gate, a
scoring/fusion engine), a green sampled-test suite is necessary but never sufficient.
Run a separate adversarial pass constructing inputs the tests don't cover, trace by
hand, and turn every confirmed repro into a permanent regression vector. See
`feedback_conformance_green_not_correct` and `implementing-features`'s Phase 5c
(Adversarial Review).

<!-- Add lessons below this line -->
