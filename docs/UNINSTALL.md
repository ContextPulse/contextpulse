# Uninstalling ContextPulse

## 1. Stop the daemon

```bash
# If running as a foreground process, press Ctrl+C.
# If running in the background:
python -m contextpulse_core.daemon --stop
```

On Windows, you can also end the process from Task Manager (look for `python.exe` or `pythonw.exe` running `contextpulse_core.daemon`).

## 2. Remove scheduled tasks (if configured)

**Windows Task Scheduler:**
1. Open Task Scheduler
2. Find and delete any ContextPulse-related tasks (e.g., canary health check)

**macOS/Linux cron:**
```bash
crontab -e
# Remove any lines referencing contextpulse
```

## 3. Uninstall Python packages

```bash
pip uninstall contextpulse-core contextpulse-sight contextpulse-voice contextpulse-touch contextpulse-project contextpulse-memory
```

## 4. Remove MCP server configuration

Remove the `contextpulse` entry from your MCP client config:

- **Claude Code:** `~/.claude.json` -- delete the `"contextpulse"` key under `mcpServers`
- **Cursor:** `.cursor/mcp.json` -- delete the `"contextpulse"` key
- **Continue:** `~/.continue/config.yaml` or `config.json` -- remove the contextpulse server entry

## 5. Delete local data

ContextPulse stores all data locally. To remove it:

```bash
# Activity database and screenshots (default location)
rm -rf ~/screenshots/

# Or check your configured output directory:
# echo $CONTEXTPULSE_OUTPUT_DIR
```

The SQLite database (`activity.db`), screenshot buffer, and heartbeat file all live in the output directory (default: `~/screenshots/`).

## 6. Remove source code (if cloned)

```bash
rm -rf /path/to/contextpulse
```

## What ContextPulse does NOT leave behind

- No registry entries (Windows)
- No launch agents or daemons (macOS/Linux) unless you manually created them
- No cloud accounts or remote data
- No browser extensions or system drivers
