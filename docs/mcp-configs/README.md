# MCP Configuration Examples

ContextPulse exposes its tools via [Model Context Protocol (MCP)](https://modelcontextprotocol.io) over streamable HTTP on `http://127.0.0.1:8420/mcp`.

**The endpoint requires an access token.** It serves screen OCR, clipboard history, keystroke bursts, transcriptions and memory recall, and binding to loopback does not scope by caller — without a token, any process on the machine, under any account, can call every tool. Every snippet below therefore sends an `Authorization: Bearer` header.

## Where is my token?

ContextPulse generates one random token per install, on first use, and stores it with user-only file permissions:

| OS | Path |
|---|---|
| Windows | `%APPDATA%\ContextPulse\mcp_token` |
| macOS | `~/Library/Application Support/ContextPulse/mcp_token` |
| Linux | `$XDG_CONFIG_HOME/ContextPulse/mcp_token` (usually `~/.config/ContextPulse/mcp_token`) |

Three ways to get it, all reading that same file:

```bash
contextpulse --setup                       # writes it into your clients for you
contextpulse-mcp --print-config claude-code   # prints the snippet to paste
```

or the tray menu: **Settings -> MCP Access** (show, copy, regenerate), or **Copy MCP Token**.

Treat it like a password. Anything holding it can call every tool.

## Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "contextpulse": {
      "type": "http",
      "url": "http://127.0.0.1:8420/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN_HERE" }
    }
  }
}
```

Or from the CLI:

```bash
claude mcp add --transport http -s user contextpulse http://127.0.0.1:8420/mcp \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

After editing the file, run `/mcp` in Claude Code, select `contextpulse`, and reconnect — no restart needed.

## Cursor

Add to `.cursor/mcp.json` in your project root (or global settings):

```json
{
  "mcpServers": {
    "contextpulse": {
      "url": "http://127.0.0.1:8420/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN_HERE" }
    }
  }
}
```

Cursor auto-detects the transport from the URL. No `type` field needed. Cursor also interpolates environment variables, so `"Bearer ${env:CONTEXTPULSE_MCP_TOKEN}"` works if you would rather not put the token in the file.

## Gemini CLI

Add to `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "contextpulse": {
      "httpUrl": "http://127.0.0.1:8420/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN_HERE" }
    }
  }
}
```

## Claude Desktop

Desktop's own config is stdio-only, so it reaches the HTTP endpoint through `mcp-remote` (needs Node):

```json
{
  "mcpServers": {
    "contextpulse": {
      "command": "npx",
      "args": ["mcp-remote", "http://127.0.0.1:8420/mcp", "--header", "Authorization:${AUTH_HEADER}"],
      "env": { "AUTH_HEADER": "Bearer YOUR_TOKEN_HERE" }
    }
  }
}
```

Note there is **no space** after `Authorization:` — `mcp-remote` documents an argument-mangling bug on Windows when the header value contains a space, which is why the value is passed through the environment.

## VS Code + Continue Extension

Add to your Continue config at `~/.continue/config.yaml`:

```yaml
mcpServers:
  - name: contextpulse
    url: http://127.0.0.1:8420/mcp
    requestOptions:
      headers:
        Authorization: Bearer YOUR_TOKEN_HERE
```

Continue's support for custom MCP headers has moved between versions. If yours ignores the block above, the endpoint answers `401` — use the `mcp-remote` bridge in the next section instead, which passes the header itself.

## Generic stdio wrapper (any MCP client)

If your MCP client only supports stdio transport, `mcp-remote` bridges it:

```bash
npx mcp-remote http://127.0.0.1:8420/mcp --header "Authorization:Bearer YOUR_TOKEN_HERE"
```

There is also a native stdio mode that needs no token, because the client spawns it as you, over a private pipe:

```json
{
  "mcpServers": {
    "contextpulse": {
      "command": "contextpulse-mcp",
      "args": ["--stdio"]
    }
  }
}
```

The trade-off: every client that does this starts its own process, and each one loads the Whisper and OCR models again. The HTTP endpoint exists so one process serves them all.

## Rotating the token

Settings -> MCP Access -> **Regenerate token**, or delete the token file and restart the MCP server. Either way, re-run `contextpulse --setup` to update your clients and reconnect them.

## Verifying the connection

Once configured, your MCP client should discover all ContextPulse tools automatically. Run `get_buffer_status` as a quick smoke test — it returns daemon health info with no side effects.

If the client reports the server as failed or unauthorized:

| Symptom | Cause | Fix |
|---|---|---|
| `401` | No `Authorization` header, or a stale token | Re-run `contextpulse --setup`, then reconnect |
| `403` | An `Origin` header that is not localhost | You are calling from a browser page; that is refused by design |
| `421` | A `Host` header that is not `127.0.0.1:8420`, `localhost:8420` or `[::1]:8420` | Use one of those in the URL |
| Connection refused | The MCP server is not running | Start ContextPulse; the watchdog relaunches the server within its loop |

## Turning auth off

There is one escape hatch, for debugging only:

```bash
contextpulse-mcp --no-auth                 # or: set CONTEXTPULSE_MCP_AUTH=off
```

It logs a warning banner at startup and leaves every tool callable by every local process.

The switch is deliberately hard to persist. It is not a setting in `config.json`, and **it is ignored when it comes from a `.env` file** — ContextPulse loads `.env` with `override=True`, so a `.env` in whatever directory you started the server from would otherwise beat your real environment, which is the same "persisted setting" this design rules out. A `.env` line that would have disabled auth is logged and ignored:

```
Ignoring CONTEXTPULSE_MCP_AUTH=off: it comes from a .env file, not the
environment. MCP auth stays ON.
```

Set it in the actual environment of the process, or pass `--no-auth`.
