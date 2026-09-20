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

---

### [2026-09-19] Two config systems coexisting means every settings toggle can be a placebo

**Context:** The Settings dialog wrote `config.json`. The capture daemon never imported
that module — it read an env-var-only config frozen at process start.

**Problem:** Every toggle in the dialog was a placebo. It saved, it showed the new value
on reopen, and it changed nothing in the process doing the capturing. Six fields were
fully dead, twelve misleading, five partial. The worst was a blocklist intended to stop
capture of password managers and 2FA prompts: whatever the user typed, the list was
empty in the code path that actually ran. Nothing failed, nothing logged, and the UI
confirmed the setting back to the user every time.

**Fix/Pattern:** When two config systems coexist, the question is never "does the write
work" — it is **"does the process that acts on this value read this file?"** Write a
test that sets a value through the *user-facing* path and asserts the *consumer* sees
it, for every field. That test is what makes a toggle real; a round-trip test through
the writer proves only that the writer round-trips. And treat a settings UI shipped
against an unread config as a privacy defect rather than a UI bug, because a control
that claims to stop data collection and does not is worse than no control at all.

---

### [2026-09-19] Redaction belongs at write, at the boundary, AND before any egress — one chokepoint is a chokepoint for one path

**Context:** The project had a working `redact_sensitive()` with a good pattern table,
and a reasonable belief that captured text was scrubbed.

**Problem:** It had exactly one caller. Screen-OCR text was redacted; clipboard, voice
transcripts, keystroke burst and correction text, memory and the knowledge-ingest
bridge were not. The function's existence was doing the reassuring, and nobody had
asked which paths reached it. A second surface was worse because it was invisible:
captured text was also being sent to an external model for summarisation, which is
egress that no storage-level fix touches.

**Fix/Pattern:** Redact in three places, deliberately, and say why each exists.
**(1) At write**, before persistence, before truncation — patterns have minimum
lengths, so a token split by a size cap matches nothing and its head gets stored — and
before any dedupe hash, or the hash becomes a brute-force oracle on short secrets.
**(2) At the read boundary**, because rows captured before the fix are still on disk.
**(3) Before egress**, for anything leaving the process. Then enumerate every
stored-text field and check each one individually; the audit question is *"which
fields reach the redactor"*, never *"is there a redactor."* Keeping one shared pattern
table in the lowest common package is what makes (1)–(3) agree instead of drifting.

---

### [2026-09-19] A search over raw text is an oracle even when the output is redacted

**Context:** After redaction was applied to stored values and to tool output, search
tools still matched their query against the *raw* stored text and returned a count.

**Problem:** That count is an extraction channel. Query a one-character prefix, read
the count, extend by one character, repeat — a secret is recoverable character by
character without ever appearing in any output the redactor sees. A planted key was
recovered 13 characters deep this way. Every output-side test passed throughout,
because the leak is in the *response to a query*, not in the response body.

**Fix/Pattern:** Redacting output is not sufficient for any interface that answers
questions *about* data. Ask the same question of the redacted text, through the same
tokenizer the index uses — a tokenizer mismatch quietly reintroduces the channel, so
the filter must share the tokenizer rather than approximate it. Generally: when you
add a control, enumerate what still observes the uncontrolled value — counts,
timings, lengths, existence checks, error messages — and treat each as an output.

---

### [2026-09-19] Count a public claim against a real client, not against a catalog

**Context:** The README, the project docs and the marketing site all advertised a tool
count. It had been carried forward, never measured.

**Problem:** It was wrong — the live server exposes 37 tools, not the "~35" every
document claimed. A wider audit of public claims found five outright false, three
overstated and two unverifiable. Two of the false ones were the most specific and
therefore the most damaging: a named technical feature advertised as shipped, which
covered one capture path and not the rest, and an absolute statement about data
never leaving the machine which a cloud-backed client makes false by its own
behaviour.

**Fix/Pattern:** Any number in public-facing copy needs a command that regenerates it
and a date on the last time it was run. Count by connecting a real client and
enumerating what it receives — not by counting decorators, registry entries, or docs,
all of which can drift from what is served. And audit specific claims first: a vague
slogan ages badly, but a falsifiable one ("N tools", "feature X redacts before
storage") is what a reader can check and what a reporter will check for you.

