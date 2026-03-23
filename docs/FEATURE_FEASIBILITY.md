# ContextPulse Sight — Feature Feasibility Analysis

Generated 2026-03-21 from codebase-aware technical evaluation. Updated after building 5 features.

## Summary

5 of 10 proposed features have been **built and shipped** this session. The remaining 5 range from medium to high effort.

## Feature Status

| # | Feature | Status | Complexity | Files Changed | Tests Added |
|---|---------|--------|-----------|---------------|-------------|
| 1 | Clipboard Context Capture | **BUILT** | Low-Medium | clipboard.py (new), activity.py, app.py, mcp_server.py | 10 |
| 2 | MCP Config Generator | **BUILT** | Low | setup.py (new), app.py | 4 |
| 3 | Multi-Agent Awareness | **BUILT** | Low | activity.py, mcp_server.py | 4 |
| 4 | Diff-Aware Capture | **BUILT** | Low | buffer.py, activity.py, app.py, mcp_server.py | 8 |
| 5 | Contextual Annotations | Not started | Medium | New hotkey handler, tkinter dialog, activity.py, mcp_server.py | — |
| 6 | Project-Aware Capture | Not started | Medium | Window title parsing, activity.py, mcp_server.py | — |
| 7 | Token Cost Estimation | **BUILT** | Trivial | buffer.py, mcp_server.py | 5 |
| 8 | Capture Webhooks | Not started | Medium | New event bus module, webhook HTTP client | — |
| 9 | Screen Narration | Not started | High | Model download, inference pipeline, queue | — |
| 10 | Cross-Machine Sync | Not started | High | Sync protocol, conflict resolution | — |

## Built Features — Technical Details

### 1. Token Cost Estimation (buffer.py, mcp_server.py)
- `estimate_image_tokens(w, h)` — Claude's public tile formula: `ceil(w/768) * ceil(h/768) * 258`
- `estimate_text_tokens(text)` — `max(1, len(text) // 4)` (~4 chars per token)
- `get_buffer_status()` now reports: total image tokens, avg per frame, total text tokens, and savings percentage
- **No new dependencies.** Pure math on existing data.

### 2. Diff-Aware Capture (buffer.py, activity.py, app.py, mcp_server.py)
- `RollingBuffer.add()` return type changed from `Path | False` to `(Path, float) | False`
- New `_diff_pct()` method computes 0-100% pixel difference using numpy mean absolute diff
- `diff_score` REAL column added to activity table with ALTER TABLE migration for existing DBs
- `get_recent(min_diff=50)` filters buffer frames by looking up diff_score in activity DB
- `search_by_frame(path)` method for frame-to-activity-record lookup
- **No new dependencies.** Extends existing numpy infrastructure.

### 3. MCP Config Generator (setup.py, app.py)
- `contextpulse-sight --setup claude-code|cursor|gemini|all|print`
- Reads existing config JSON, preserves other servers, injects ContextPulse entry
- Detects command path via `shutil.which("contextpulse-sight-mcp")`
- Supports: Claude Code (`~/.claude.json`), Cursor (`.cursor/mcp.json`), Gemini (`~/.gemini/settings.json`)
- **No new dependencies.** Uses stdlib json, shutil, pathlib.

### 4. Clipboard Context Capture (clipboard.py, activity.py, app.py, mcp_server.py)
- `ClipboardMonitor` class — polling-based (1s interval) using Win32 `GetClipboardSequenceNumber` + `GetClipboardData`
- Filters: min 5 chars, deduplication of consecutive identical clips, 1s debounce, 10K char truncation
- New `clipboard` table in SQLite with timestamp + text columns
- `record_clipboard()`, `get_clipboard_history()`, `search_clipboard()` methods on ActivityDB
- Two new MCP tools: `get_clipboard_history(count)`, `search_clipboard(query, minutes_ago)`
- Wired into daemon lifecycle (start/stop alongside other monitors)
- **No new dependencies.** Uses ctypes for Win32 clipboard API (already used for mutex).

### 5. Multi-Agent Awareness (activity.py, mcp_server.py)
- New `mcp_calls` table: timestamp, tool_name, client_id
- `@_track_call` decorator on all 10 MCP tools logs each call
- `record_mcp_call()` and `get_agent_stats(hours)` methods on ActivityDB
- New `get_agent_stats()` MCP tool shows per-client call breakdown
- **No new dependencies.**

## Remaining Features — Feasibility Assessment

### 5. Contextual Annotations (Medium)
- **Can reuse:** gui_theme.py (tkinter dialogs), activity.py (storage), hotkey system in app.py
- **New deps needed:** None
- **Key risk:** tkinter input dialog needs to not block capture thread. Use `after()` scheduling.
- **Estimate:** ~200 LOC. New hotkey (Ctrl+Shift+N), small input dialog, annotation column in activity table, search integration.

### 6. Project-Aware Capture (Medium)
- **Can reuse:** privacy.py (window title reading), activity.py (storage)
- **New deps needed:** None
- **Key risk:** Window title formats vary across IDEs (VS Code: "file - Project", PyCharm: "Project - file", etc.). Needs a heuristic pattern registry.
- **Estimate:** ~250 LOC. Parse window titles, detect git repo root from terminal cwd, new project_name column in activity, new MCP tool.

### 7. Capture Webhooks / Event Stream (Medium)
- **Can reuse:** events.py (event detection patterns)
- **New deps needed:** `aiohttp` or `httpx` for webhook POST (or keep it simple with urllib)
- **Key risk:** Webhook delivery reliability, retry logic, configuration surface area.
- **Estimate:** ~300 LOC. Event bus, webhook config, HTTP POST client, event filtering.

### 8. Screen Narration (High)
- **New deps needed:** `moondream` or `transformers` + model download (~3.5GB)
- **Key risk:** CPU inference latency (2-5s per frame), memory usage (~2GB for model), first-run model download UX.
- **Estimate:** ~400 LOC + model management. Queue-based inference, narration storage, search integration.
- **Recommendation:** Defer until after the Memory package. The model management problem is significant.

### 9. Cross-Machine Sync (High)
- **New deps needed:** Sync protocol (could use SQLite WAL + OneDrive/Dropbox)
- **Key risk:** Conflict resolution, partial sync, network failures, encryption requirements.
- **Estimate:** ~500+ LOC. Best deferred until Memory package provides a sync layer.
- **Recommendation:** Wait for Memory package.

## Revised Build Order (remaining)

1. **Contextual Annotations** — highest unique value, medium effort, no new deps
2. **Project-Aware Capture** — high value for multi-project devs, medium effort
3. **Capture Webhooks** — platform play, enables automation ecosystem
4. **Screen Narration** — game-changer but needs model management solved first
5. **Cross-Machine Sync** — defer until Memory package
