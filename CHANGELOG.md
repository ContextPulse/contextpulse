# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
