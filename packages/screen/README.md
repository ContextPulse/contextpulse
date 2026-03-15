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

All settings via environment variables (or `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `CONTEXTPULSE_OUTPUT_DIR` | `~/screenshots` | Where captures are saved |
| `CONTEXTPULSE_AUTO_INTERVAL` | `5` | Auto-capture interval (seconds, 0=disabled) |
| `CONTEXTPULSE_BUFFER_MAX_AGE` | `180` | Buffer retention (seconds) |
| `CONTEXTPULSE_CHANGE_THRESHOLD` | `1.5` | Min % pixel diff to store frame |
| `CONTEXTPULSE_BLOCKLIST` | *(empty)* | Comma-separated window title blocklist |
| `CONTEXTPULSE_BLOCKLIST_FILE` | *(empty)* | Path to blocklist file (one pattern per line) |

## Privacy

- **Window title blocklist** — skip captures when sensitive apps are focused
- **Auto-pause on lock** — pauses when you press Win+L, resumes on unlock
- **Manual pause** — Ctrl+Shift+P or system tray menu
- All processing is local. No data leaves your machine.

## Requirements

- Windows 10/11
- Python 3.12+
