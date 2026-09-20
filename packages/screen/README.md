# ContextPulse Sight

Always-on screen capture for AI coding assistants. Your AI can see what you see.

ContextPulse Sight runs as a background daemon, continuously capturing your desktop and serving screenshots to AI agents via [MCP](https://modelcontextprotocol.io) (Model Context Protocol).

## Install

```bash
pip install contextpulse-sight
```

## Quick Start

**Start the daemon** (system tray + auto-capture):
```bash
contextpulse-sight
```

**Start the MCP server** (for Claude Code / AI assistants):
```bash
contextpulse-sight-mcp
```

### Claude Code MCP config

Add to your `.mcp.json`:
```json
{
  "mcpServers": {
    "contextpulse-sight": {
      "command": "contextpulse-sight-mcp",
      "args": []
    }
  }
}
```

## Hotkeys

Defaults. All four are settable in the Settings dialog (`hotkey_capture`,
`hotkey_all_monitors`, `hotkey_region`, `hotkey_pause`) as
`modifier+...+letter`, e.g. `ctrl+alt+k`. A changed hotkey takes effect on
the next daemon restart.

| Hotkey | Action |
|--------|--------|
| Ctrl+Shift+S | Quick capture (active monitor) |
| Ctrl+Shift+A | All monitors (stitched panorama) |
| Ctrl+Shift+Z | Region (800x600 around cursor) |
| Ctrl+Shift+P | Pause / Resume |

## MCP Tools

| Tool | Description |
|------|-------------|
| `get_screenshot(mode)` | Capture screen — "active", "all", or "region" |
| `get_recent(count, seconds)` | Recent frames from rolling buffer |
| `get_screen_text()` | OCR current screen at full resolution |
| `get_buffer_status()` | Check daemon health and buffer state |

## Configuration

Settings live in `config.json` (`%APPDATA%/ContextPulse/config.json` on
Windows) and are edited through the tray **Settings** dialog. Every one of
them can also be set with an environment variable, which **overrides** the
saved value.

| Variable | Default | Description |
|----------|---------|-------------|
| `CONTEXTPULSE_OUTPUT_DIR` | `~/screenshots` | Where captures are saved (env only — a path is bound at startup) |
| `CONTEXTPULSE_AUTO_INTERVAL` | `5` | Auto-capture interval (seconds, 0=disabled) |
| `CONTEXTPULSE_AUTO_INTERVAL_IDLE` | `30` | Stretched interval once you have been idle |
| `CONTEXTPULSE_AUTO_IDLE_THRESHOLD` | `60` | Seconds of no events before switching to the idle interval |
| `CONTEXTPULSE_BUFFER_MAX_AGE` | `1800` | Buffer retention (seconds) |
| `CONTEXTPULSE_CHANGE_THRESHOLD` | `0.5` | Min % pixel diff to store frame |
| `CONTEXTPULSE_OCR_DIFF_THRESHOLD` | `5.0` | Min % pixel diff to OCR a stored frame (0 = always OCR) |
| `CONTEXTPULSE_JPEG_QUALITY` | `90` | JPEG quality for stored frames (1-100) |
| `CONTEXTPULSE_STORAGE_MODE` | `smart` | `smart` / `visual` / `both` / `text` |
| `CONTEXTPULSE_ACTIVITY_MAX_AGE` | `86400` | Activity-DB retention (seconds) |
| `CONTEXTPULSE_ALWAYS_BOTH` | `thinkorswim.exe` | Comma-separated apps that keep image **and** text |
| `CONTEXTPULSE_BLOCKLIST` | *(14 built-in patterns)* | Comma-separated window title blocklist |
| `CONTEXTPULSE_BLOCKLIST_FILE` | *(empty)* | Path to blocklist file (one pattern per line) |

`CONTEXTPULSE_BLOCKLIST` **replaces** the list, so setting it also discards
the 14 built-in patterns; `CONTEXTPULSE_BLOCKLIST_FILE` **appends** to it.

Values are re-read while the daemon runs, so a change in the Settings dialog
takes effect on the next capture — except the four hotkeys and the capture
paths, which are bound at startup and need a restart. The dialog says so.

## Privacy

- **Window title blocklist** — skip captures when sensitive apps are focused
- **Auto-pause on lock** — pauses when you press Win+L, resumes on unlock
- **Manual pause** — Ctrl+Shift+P or system tray menu
- All processing is local. No data leaves your machine.

## Requirements

- Windows 10/11
- Python 3.12+
