# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Cross-modal learning pipeline** — automatic vocabulary corrections from Sight + Voice + Touch temporal correlations
- **Energy-based silence detection** — recording continues until the user stops speaking (up to 2s tail) instead of hard-cutting on key release
- **Tray icon resilience** — daemon auto-restarts the system tray icon up to 5 times when Windows kills the notification area
- **Watchdog single-instance guard** — named mutex prevents zombie watchdog chains
- **Whisper small model upgrade** — upgraded from tiny to small with model-specific threshold profiles and monotonicity tests
- **Monitor summary tool** — lightweight text summary of all monitors (~50-100 tokens vs ~1,200 for an image)
- **Smart screenshot mode** — only captures monitors that changed recently
- **DXcam integration** — hardware-accelerated screen capture on Windows
- **Adaptive region capture** — 800x600 region around cursor for focused context
- **EventBus hooks** — buffer status and event timeline query tools
- **Licensing module** — Gumroad webhook + Ed25519 signing for Pro feature delivery
- **Pro feature gates** — config-driven `pro_features.yaml` with `@_require_pro` decorator
- **Integration test suite** — thread safety, GUI survival, daemon lifecycle tests
- **Property-based testing** — hypothesis for fuzzy input validation
- **macOS production readiness** — TCC permissions, CoreGraphics OCR, tray, transcriber, packaging, CI
- **300ms trailing audio buffer** — captures speech that overlaps with hotkey release
- **PII redaction engine** — strips API keys, passwords, SSNs from OCR output
- **Context-aware vocabulary** — auto-builds voice vocabulary from PROJECT_CONTEXT.md files

### Changed

- Disabled Whisper quality filters (log_prob, compression_ratio) that silently dropped transcript segments
- Tail buffer increased from 300ms to 700ms minimum before silence detection kicks in
- Per-segment diagnostic logging at INFO level for production transcription monitoring
- Disabled pyautogui failsafe to prevent paste pipeline crashes when mouse is at screen origin

### Fixed

- Voice hotkey crash on rapid press/release
- Whisper sentence cutoff caused by model-specific threshold mismatch
- Settings dialog killing the daemon process
- Test isolation issues across parallel test runners
- CI skip marker for platform-specific tests

### Security

- SPDX license headers on all source files
- gitleaks secret scanning with pre-commit hook
- Removed internal documentation and PII from public export
- Excluded Lambda deploy scripts containing S3 bucket and SSM paths

## [0.1.0] - 2026-03-28

### Added

- Unified daemon architecture — one process, one tray icon, three modules
- **Sight** module: screen capture, OCR, clipboard monitoring (10 MCP tools)
- **Voice** module: hotkey dictation via faster-whisper, vocabulary hot-reload (8 MCP tools)
- **Touch** module: keyboard/mouse activity capture, correction detection (5 MCP tools)
- **Project** module: project-aware content routing across your portfolio (5 MCP tools)
- **Memory** module: three-tier persistent memory (hot/warm/cold) with FTS5 search
- EventBus spine architecture for cross-module event sharing
- Platform abstraction layer (Windows, macOS, Linux)
- 882 tests across all packages
- GitHub Actions CI (lint + test)
- Pre-commit hooks (ruff lint/format, trailing whitespace, YAML/TOML checks)
- AGPL-3.0 open-core license
- Brand kit, landing page, and Cloudflare Pages deployment
- PII redaction engine for screen captures
- Context-aware voice vocabulary learning from PROJECT_CONTEXT.md files
