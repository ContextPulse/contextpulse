# MCP Configuration Examples

ContextPulse exposes its tools via [Model Context Protocol (MCP)](https://modelcontextprotocol.io) over streamable HTTP on `http://127.0.0.1:8420/mcp`.

Below are configuration snippets for popular MCP clients. Pick the one that matches your editor/tool.

## Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "contextpulse": {
      "type": "http",
      "url": "http://127.0.0.1:8420/mcp"
    }
  }
}
```

## Cursor

Add to `.cursor/mcp.json` in your project root (or global settings):

```json
{
  "mcpServers": {
    "contextpulse": {
      "url": "http://127.0.0.1:8420/mcp"
    }
  }
}
```

Cursor auto-detects the transport from the URL. No `type` field needed.

## VS Code + Continue Extension

Add to your Continue config at `~/.continue/config.yaml`:

```yaml
mcpServers:
  - name: contextpulse
    url: http://127.0.0.1:8420/mcp
```

Or in `~/.continue/config.json`:

```json
{
  "mcpServers": [
    {
      "name": "contextpulse",
      "url": "http://127.0.0.1:8420/mcp"
    }
  ]
}
```

## Generic stdio wrapper (any MCP client)

If your MCP client only supports stdio transport, you can use `mcp-remote` as a bridge:

```bash
npx mcp-remote http://127.0.0.1:8420/mcp
```

Example stdio config for clients that need a command-based server:

```json
{
  "mcpServers": {
    "contextpulse": {
      "command": "npx",
      "args": ["mcp-remote", "http://127.0.0.1:8420/mcp"]
    }
  }
}
```

## Verifying the connection

Once configured, your MCP client should discover all ContextPulse tools automatically. Run `get_buffer_status` as a quick smoke test -- it returns daemon health info with no side effects.
