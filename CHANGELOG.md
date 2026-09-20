# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-09-19

### Security

- Clipboard text is now redacted before storage and at the MCP boundary; the
  `clipboard_enabled` setting is now honoured. Reported by Yevhen Tienkaiev.

### Added

- `scripts/purge_clipboard_secrets.py` — scrubs secrets from clipboard and
  event rows written before this release. Dry run by default; reports counts
  by category only.

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
