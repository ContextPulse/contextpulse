# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-09-19

### Security

Secret redaction now covers **every modality that stores text**, not only the
clipboard. The clipboard report was the one that came in; the same defect was
present in voice, touch, memory and the two derived stores.

- **Clipboard** — text is redacted before storage and at the MCP boundary, and
  the `clipboard_enabled` setting is now honoured. Reported by Yevhen Tienkaiev.
- **Voice** — transcripts (both raw and cleaned) are redacted before the event
  is stored, and on the way out of `get_recent_transcriptions`,
  `learn_from_session`, `get_vocabulary`, `rebuild_context_vocabulary` and
  `check_corrections`. What you dictate still reaches your cursor verbatim;
  only the stored copy is scrubbed.
- **Touch** — typed-correction text is redacted before storage and at the
  boundary. The touch MCP module's docstring claimed "Privacy-safe: shows
  activity patterns, not keystrokes", which was true for typing bursts and
  false for the `corrections` filter; corrected.
- **Memory** — values are redacted before reaching any tier, and on the way out
  of `memory_recall`, `memory_search`, `memory_semantic_search` and
  `memory_list`.
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
- **Search no longer leaks through its result count.** `search_clipboard`
  matched raw stored text and the tool printed the match count, so a caller
  could recover a pre-release secret one character at a time while every
  response it saw was correctly redacted. Matching is now done against redacted
  text, and `search_all_events` drops results that matched only on redacted
  content.
- **Rows written before this release are swept once, automatically**, on the
  first daemon or MCP-server start after upgrade, across all three databases.
- **Redaction patterns widened**: OpenSSH/EC/generic private-key blocks (the
  module claimed to cover these and did not), any `scheme://user:pass@` URL,
  HTTP Basic, Slack, Stripe, Google, npm, fine-grained GitHub and Twilio
  tokens, 15-digit Amex, and a bare AWS secret key when it appears alongside an
  access key id. A token glued to a word character (common in OCR) is now
  matched — every pattern was previously word-boundary anchored on both ends.
- **Log files no longer carry captured text.** Six lines that wrote clipboard,
  dictation or correction content to a rotating log now record lengths.
- Live screen OCR (`get_screen_text`) honours the `redact_ocr_text` setting,
  which previously applied only at write time.

### Added

- `scripts/purge_clipboard_secrets.py` — scrubs secrets from clipboard, event,
  knowledge-graph and probe-fact rows written before this release. Dry run by
  default; reports counts by category only, never a value. The pre-purge backup
  is deleted once the run verifies itself, since it holds the raw values;
  `--keep-backup` retains it with a warning.
- `memory_store` rejects values over 64 KB with a clear error rather than
  storing them unbounded.
- A "Capture clipboard contents" checkbox in Settings, and the matching
  `CONTEXTPULSE_CLIPBOARD_ENABLED` environment variable.

### Fixed

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
