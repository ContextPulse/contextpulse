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

### [2026-09-19] A gate that measures an experiment nobody ran is not evidence
**Original lesson:** **Context:** The Phase 0 wedge probe reached its documented STOP on 2026-08-20 at
"0 of 3 attributed saves." That number had been treated as a verdict on whether the
knowledge graph earns its keep.

**Problem:** It was never a verdict on anything. Running the attribution instrument
showed `tool_usage` held **one row for all time** — `facts_about('1Password')`,
2026-08-24 — against 1000 accumulated facts. `facts_about` and `context_at` were
exposed over MCP and working, but `using-contextpulse` (the skill that documents
~35 ContextPulse tools) never mentioned either. Its only two matches for
`context_at` were `get_context_at`, the unrelated Sight screen-frame tool. No agent
had any way to learn the recall tools existed, so the experiment ran for two months
with no treatment arm.

Compounding it: `PROJECT_CONTEXT.md` listed "build the attribution instrument" as the
highest-value *unstarted* work. It had been built, wired into every real call,
covered by tests, and was reporting correctly the whole time.

**Fix/Pattern:** Three habits, in order of how much they would have saved:
1. **Run the instrument before trusting the number it produces.** One command
   separated "recall is useless" from "recall was never invoked" — opposite problems
   with opposite fixes.
2. **A capability nobody is routed to does not exist.** Shipping an MCP tool is not
   the same as making it reachable. The skill is the front door; a tool absent from it
   is unreachable no matter how well it works.
3. **A gate must distinguish "not attempted" from "attempted and failed."** The
   restart carries a two-stage gate for exactly this: a precondition on call count
   before any judgement about save count.

**Archived:** 2026-09-19 (trim-lessons.py) — **VERIFIED encoded** in `using-contextpulse` SKILL.md, marked at line 287 by its `<!-- lesson: -->` stamp.

### [2026-09-19] A deliberate version cap needs an ignore rule, or a bot will undo it
**Original lesson:** **Context:** Widened Dependabot to watch the eight `packages/*/pyproject.toml` manifests,
which had never been monitored at all.

**Problem:** Within ten minutes it opened a PR raising `mcp` from `>=1.0,<2` to `<3`
across six manifests. That cap exists because `mcp` 2.0.0 removed
`mcp.server.fastmcp` — which this project uses — and broke every CI job when it shipped.
Merging would have re-broken the workspace.

**Fix/Pattern:** `versioning-strategy` does not express intent. Both `auto` and
`increase-if-necessary` propose widening a range when a release lands outside it, which
is right for an accidental bound and wrong for a deliberate one. Only an explicit
`ignore` rule encodes "this cap is a guard" — and **it must use `versions:`, not
`update-types:`**:

```yaml
ignore:
  - dependency-name: "mcp"
    versions: [">=2.0.0"]                              # works
    # update-types: ["version-update:semver-major"]    # does NOT work here
```

**This correction is itself the lesson.** The `update-types` form was written first, folded
into a skill as settled guidance, and was wrong. Proven as a controlled pair — same repo,
same dependencies, same trigger, only the form differing:

| Dependabot run | Ignore form in its job definition | Outcome |
|---|---|---|
| 22:37 | `update-types: semver-major`, `version-requirement: null` | **opened the PR anyway** |
| 23:19 | `version-requirement: >=2.0.0` | **no PR** |

A range-widening update on a library with no lockfile has no single resolved "current
version" for a semver comparison to classify against, so the `update-types` filter never
engages.

**Verify a suppression rule by observing the thing not happen, or by reading the consumer's
loaded state — never by the fact that the file now contains the rule:**
`gh run view <id> --log | grep "Job definition"` → `job.ignore-conditions`.

Two related traps: closing a *grouped* PR creates no ignore at all, and
`@dependabot ignore this major version` does not work on one either — Dependabot says so in
its own close comment. And `versioning-strategy: widen` is **not valid for pip**; an invalid
value makes Dependabot reject the *entire file*, silently disabling every other setting in it.

**Archived:** 2026-09-19 (trim-lessons.py) — **VERIFIED encoded** in `developing-python` SKILL.md, marked at line 836 by its `<!-- lesson: -->` stamp.
