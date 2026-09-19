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

---

### [2026-09-19] A gate that measures an experiment nobody ran is not evidence

**Context:** The Phase 0 wedge probe reached its documented STOP on 2026-08-20 at
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

### [2026-09-19] Specifying a gate against a report reading the report cannot produce

**Context:** Having just diagnosed the above, I wrote the replacement gate as
"≥ 20 `tool_usage` rows after 2026-09-19, checked by
`probe_usage_report.py --since 2026-09-19`."

**Problem:** That command cannot produce that number. `--since` filters only the
confirmed-save count; the tool-call figure is all-time and ignores it. Proof: with
`--since` set, the report read 1 before a call made that day and 2 after — a filtered
count would have read 0 then 1. I had specified a measurement in terms of an
instrument reading without checking the instrument produced that reading — **the same
class of error I had just finished diagnosing.**

**Fix/Pattern:** After writing any gate, run the exact command it names and confirm the
number you specified appears in the output. Restating it as an absolute against a
recorded baseline (2 calls at 2026-09-19 16:09, so the gate is "≥ 22") is a workaround;
the real fix is `cp-probe-usage-report-since-ignores-tool-calls`.

### [2026-09-19] "Reports OK" is not the same as "worked" — an empty parse is a fault

**Context:** The nightly consolidator had silently stopped adding facts. The last write
was 2026-09-13; runs since kept printing `OK`.

**Problem:** `parse_facts()` returns `[]` on every failure mode by design and never
raises, and the caller recorded `error=None` and printed `OK`. A run that read 1500
events, returned in 6.4s and extracted nothing looked identical to a quiet day. The
CLI's stdout — the only thing that would have named the cause — was discarded.

**Fix/Pattern:** Distinguish the three outcomes a tolerant parser collapses into one:
content found, a legitimately empty result, and unparseable output. Only the third is a
fault. Keying the alarm on a bare zero count would have cried wolf constantly, since
zero-fact runs on small windows are historically common. Also record the call duration:
6.4s against a 42–64s baseline was the only signal anything was wrong, and nothing was
storing it.

### [2026-09-19] Reproduce in the real context before blaming the context

**Context:** Having found the consolidator failing on a schedule and working from a
shell, I diagnosed the *scheduled context* (Task Scheduler → wscript → cmd) as the cause
and wrote that up.

**Problem:** Wrong. Triggering the real scheduled task deliberately — `schtasks /Run` —
rather than waiting for its next run, it succeeded in its genuine scheduled context:
1500 events, 31.8s, 20 facts. The execution context was never the variable. The likely
cause is usage-limit responses from the CLI, which return quickly with exit 0 and no
JSON, matching the 6.4s signature and correlating with the day the weekly limit was hit.

**Fix/Pattern:** When a job behaves differently on a schedule, **trigger the schedule**
instead of reasoning about what the scheduler might do differently. It cost one command
and overturned a written conclusion. Corollary: never wait for the next scheduled run to
test a hypothesis you can trigger now.

### [2026-09-19] A deliberate version cap needs an ignore rule, or a bot will undo it

**Context:** Widened Dependabot to watch the eight `packages/*/pyproject.toml` manifests,
which had never been monitored at all.

**Problem:** Within ten minutes it opened a PR raising `mcp` from `>=1.0,<2` to `<3`
across six manifests. That cap exists because `mcp` 2.0.0 removed
`mcp.server.fastmcp` — which this project uses — and broke every CI job when it shipped.
Merging would have re-broken the workspace.

**Fix/Pattern:** `versioning-strategy` does not express intent. Both `auto` and
`increase-if-necessary` propose widening a range when a release lands outside it, which
is right for an accidental bound and wrong for a deliberate one. **Only an explicit
`ignore` rule with `version-update:semver-major` encodes "this cap is a guard."** Any
repo that pins a major on purpose needs both the pin and the matching ignore rule.
Secondary: `versioning-strategy: widen` is **not valid for pip** and an invalid value
makes Dependabot reject the *entire file*, silently disabling every other setting in it.
