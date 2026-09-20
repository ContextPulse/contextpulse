# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.1.1] - 2026-09-19

### Added

- The local MCP endpoint now requires a per-install bearer token. The token is
  generated on first use, stored with user-only file permissions, and surfaced
  in the tray under Settings -> MCP Access, via `contextpulse --setup`, and via
  `contextpulse-mcp --print-config <client>`.
- `contextpulse-mcp --print-config [claude-code|cursor|gemini|claude-desktop]`.

### Changed

- `contextpulse --setup` writes an authenticated `http` entry under the server
  name `contextpulse` and removes the stale stdio `contextpulse-sight` entry
  earlier builds wrote. Other servers in the config are preserved.
- Host/Origin protection is now configured explicitly rather than relying on
  FastMCP's localhost auto-default, and the allowed hosts pin the port.
- Minimum `mcp` is now 1.26.

### Breaking

- Existing MCP clients get `401` until their config gains the `Authorization`
  header. Fix: `contextpulse --setup`, restart the MCP server, reconnect the
  client. `CONTEXTPULSE_MCP_AUTH=off` or `contextpulse-mcp --no-auth` restores
  the old unauthenticated behaviour and logs a warning banner; there is no
  other grace path and it is not persistable in `config.json`.

### Security

Secret redaction now covers **every modality that stores text**, not only the
clipboard. The clipboard report was the one that came in; the same defect was
present in voice, touch, memory and the two derived stores.

- **Clipboard** — text is redacted before storage and at the MCP boundary, and
  the `clipboard_enabled` setting is now honoured. Reported privately by an
  external researcher.
- **Voice** — transcripts (both raw and cleaned) are redacted before the event
  is stored, and on the way out of `get_recent_transcriptions`,
  `learn_from_session`, `get_vocabulary`, `rebuild_context_vocabulary` and
  `check_corrections`. What you dictate still reaches your cursor verbatim;
  only the stored copy is scrubbed.
- **Touch** — typed-correction text is redacted before storage and at the
  boundary. The touch MCP module's docstring claimed "Privacy-safe: shows
  activity patterns, not keystrokes", which was true for typing bursts and
  false for the `corrections` filter; corrected.
- **Memory** — the key, the value and the tags are all redacted before reaching
  any tier, and on the way out of `memory_recall`, `memory_search`,
  `memory_semantic_search` and `memory_list`. All three are full-text indexed,
  so a memory stored *under* a secret-shaped key was as exposed as one that
  contained it. Note the consequence: a key is stored in its redacted form, so
  a secret-shaped key cannot be recalled under its raw spelling.
- **Knowledge graph and probe facts** — `knowledge.db` and `probe.db` hold a
  second and third copy of captured text. Both are now redacted at ingest and
  at the boundary (`search_knowledge`, `facts_about`, `context_at`,
  `kg_timeline`).
- **Vocabulary learning** — a correction pair is word-level, so a word can be a
  whole token. Pairs whose text matches a secret pattern are no longer learned.
- **Nothing carrying a secret pattern leaves the machine.** Voice LLM cleanup
  falls back to rule-based cleanup rather than sending such a dictation to the
  API; vocabulary analysis redacts before sending; the nightly consolidator's
  prompt is redacted before it reaches the model.
- **No search tool leaks through its result count.** A search that matched raw
  stored text while redacting only what it rendered let a caller recover a
  pre-release secret one character at a time — every response it saw was
  correctly redacted, and the match COUNT told it whether the guess was right.
  Every search surface now matches against redacted text: `search_clipboard`,
  `search_history`, `search_all_events`, `memory_search` and
  `memory_semantic_search`. For the FTS-backed ones the query is re-run against
  an index built over the redacted text using the same tokenizer, so stemmed
  and prefix queries keep working and a stemmed probe cannot route around the
  filter.
- `search_clipboard` scans the 2000 most recent entries in the window rather
  than the whole of it, which is what makes redacted matching affordable. When
  that cap binds the response now says `truncated: true` with the numbers, so
  "no results" is never silently partial.
- **Rows written before this release are swept once, automatically**, on the
  first daemon or MCP-server start after upgrade — `activity.db` (clipboard,
  events and the `activity` OCR table), `probe.db`, `knowledge.db`, `memory.db`
  and `memory_cold.db`, with their full-text indexes rebuilt. Each store is
  marked done only after a re-scan of it finds nothing, and each carries its own
  marker, so a store that was locked or that a mid-sweep quit interrupted is
  retried on the next start instead of being skipped for good.
- **Redaction patterns widened**: OpenSSH/EC/generic private-key blocks (the
  module claimed to cover these and did not), any `scheme://user:pass@` URL,
  HTTP Basic, Slack, Stripe, Google, npm, fine-grained GitHub and Twilio
  tokens, 15-digit Amex, and a bare AWS secret key when it appears alongside an
  access key id. A token glued to a word character (common in OCR) is now
  matched — every pattern was previously word-boundary anchored on both ends.
  A `-----BEGIN ... PRIVATE KEY-----` block with no closing armour is matched
  too; the previous rule required the pair, so a key truncated by OCR, by a
  scrolled terminal or by the clipboard length cap was stored verbatim.
- **Fewer false positives, because a false positive is now permanent.** The
  sweep above rewrites stored rows, so text wrongly identified as a secret is
  corrupted for good. Three rules were tightened: HTTP Basic no longer fires on
  any long word ("Basic responsibilities of the role"), the glued `sk-` rule no
  longer fires inside hyphenated phrases ("task-oriented-dialogue-system-
  evaluation"), and card numbers must now pass a Luhn check and carry a real
  issuer prefix rather than merely being sixteen digits. The card trade-off:
  a number whose digits were misread by OCR no longer matches.
- Voice's `paste_text_hash`, which correlates a dictation with the paste it
  produced, is computed over the redacted transcript. A short digest of raw
  text is a preimage oracle for a dictated card number or SSN.
- **Log files no longer carry captured text.** Six lines that wrote clipboard,
  dictation or correction content to a rotating log now record lengths.
- Live screen OCR (`get_screen_text`) honours the `redact_ocr_text` setting,
  which previously applied only at write time.

### Added

- `scripts/purge_clipboard_secrets.py` — scrubs secrets from clipboard, event,
  OCR-activity, knowledge-graph, probe-fact and memory rows written before this
  release, and its "verified: 0 remaining" line covers every one of them.
  Dry run by default; reports counts by category only, never a value. The pre-purge backup
  is deleted once the run verifies itself, since it holds the raw values;
  `--keep-backup` retains it with a warning.
- `memory_store` rejects values over 64 KB with a clear error rather than
  storing them unbounded.
- A "Capture clipboard contents" checkbox in Settings, and the matching
  `CONTEXTPULSE_CLIPBOARD_ENABLED` environment variable.
- **Seven settings that existed only as hardcoded constants are now real
  config keys**, settable in `config.json` and by environment variable like
  every other tunable: `auto_interval_idle`
  (`CONTEXTPULSE_AUTO_INTERVAL_IDLE`), `auto_idle_threshold`
  (`CONTEXTPULSE_AUTO_IDLE_THRESHOLD`), `ocr_diff_threshold`
  (`CONTEXTPULSE_OCR_DIFF_THRESHOLD`), `touch_burst_timeout`
  (`CONTEXTPULSE_TOUCH_BURST_TIMEOUT`), `touch_correction_window`
  (`CONTEXTPULSE_TOUCH_CORRECTION_WINDOW`), `touch_min_burst_chars`
  (`CONTEXTPULSE_TOUCH_MIN_BURST_CHARS`) and `touch_mouse_debounce`
  (`CONTEXTPULSE_TOUCH_MOUSE_DEBOUNCE`).
- Values in `config.json` are now range-checked the same way environment
  variables always were, so a hand-edited `"jpeg_quality": 500` is clamped
  instead of reaching the encoder.

### Changed

- **The default privacy blocklist is now in effect.** ContextPulse has always
  shipped fourteen default patterns — password managers, sign-in and
  two-factor windows — and documented them as the privacy behaviour, but the
  capture pipeline read a separate, empty list, so the defaults never blocked
  anything. Windows whose titles contain those patterns are no longer
  captured, and rows already stored with such titles stop appearing in MCP
  search results. If you want the old behaviour, set `blocklist_patterns` to
  `[]` in `config.json`.
- Blocklist patterns now match on word boundaries rather than as bare
  substrings, so the short defaults `"Sign in"` and `"Log in"` no longer block
  unrelated windows such as "Design in Figma" or "Blog index".
- Settings that cannot take effect until a restart are now named individually
  when you save, instead of a blanket "Hotkey changes will take effect after
  restarting" that appeared even when no hotkey had changed.

### Fixed

- **The Settings dialog silently downgraded two settings every time you
  pressed Save.** It carried its own copy of every default, and two had
  drifted from the values the daemon runs: it wrote `jpeg_quality` 75 over 90,
  and the Whisper model `base` over `small`. Opening Settings and saving with
  nothing changed was enough to trigger it.
- **"Always use AI cleanup" did nothing until the next restart.** The voice
  module read the setting once at startup and cached it; it is now read per
  dictation.
- **Setting the auto-capture interval to 0 could not be undone.** The capture
  and watchdog threads were only created when the interval was above zero at
  startup, so a zero left the process with nothing to restart — and took the
  clipboard setting's 15-second reconcile down with it, because that runs on
  the same watchdog. Both threads now always start and a zero interval is
  handled inside the loop.
- The four touch settings in `config.json` were written by the Settings dialog
  and then ignored by the daemon, which read hardcoded constants instead.
- An unparseable value in one of the touch fields discarded the entire Save —
  blocklist, hotkeys and all — with no message.
- `CONTEXTPULSE_ACTIVITY_DB` was resolved independently in three places; the
  daemon now imports the one path the rest of the codebase uses.

- Boolean environment variables were parsed as integers, so
  `CONTEXTPULSE_KNOWLEDGE_ENABLED=true` raised `ValueError` out of every
  `config.get()` caller, and `=1` / `=0` yielded the integers 1 and 0 rather
  than `True` and `False`.
- The clipboard setting is reconciled on its own timer, so unticking it takes
  effect mid-session even when auto-capture is disabled.
- `ClipboardMonitor.stop()` waits for its polling thread, so restarting the
  monitor can no longer run two of them briefly and double-capture a clip.

## [0.1.0] - 2026-04-11

Initial public release.

### Highlights

- **Sight** — Screen capture, OCR, clipboard monitoring (10 MCP tools)
- **Voice** — Hotkey dictation via faster-whisper with vocabulary learning (8 MCP tools)
- **Touch** — Keyboard/mouse activity capture and correction detection (5 MCP tools)
- **Project** — Project-aware content routing across your portfolio (5 MCP tools)
- **Memory** — Three-tier persistent memory (hot/warm/cold) with full-text search (6 MCP tools)
- Unified daemon architecture — one process, one tray icon, five modules
- EventBus spine for cross-module event correlation
- Platform support for Windows and macOS
- 1,050+ tests, GitHub Actions CI, pre-commit hooks
- AGPL-3.0 open-core license
