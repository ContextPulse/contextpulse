# Security Policy

ContextPulse processes sensitive data (screen captures, voice recordings, keyboard activity). We take security seriously.

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | Yes                |

## Reporting a Vulnerability

Please report security vulnerabilities by emailing **security@contextpulse.ai**.

**Do NOT open a public GitHub issue for security vulnerabilities.**

We will:
- Acknowledge receipt within **48 hours**
- Provide a detailed response within **7 days**, including next steps
- Work with you to understand and address the issue before any public disclosure

## Security Design Principles

- **Local-first**: Capture, storage and search run on your machine, and there is no telemetry or analytics.
- **Network calls are opt-in**: ContextPulse never phones home, and license verification is offline (Ed25519 signature check, no network). Three features can reach the network, and each is off until you turn it on:
  - Semantic memory search downloads the open-weights embedding model from huggingface.co the first time you use it, then runs offline. No data of yours is sent.
  - Voice LLM cleanup sends the text of a dictation to Anthropic's API. It is off unless you set an API key in `voice_anthropic_api_key` (or `ANTHROPIC_API_KEY`) and turn on `voice_always_use_llm`. The same key lets the vocabulary learner send recent raw and cleaned transcript pairs to Anthropic to spot words that speech recognition keeps mishearing.
  - The fact consolidator (`scripts/probe_consolidator.py`) sends recent captured events to the Claude CLI to extract facts. The prompt carries the app name, window title and captured text of each event, so screen OCR, transcripts, clipboard and typed text can all be in it. It runs only when you run or schedule that script. It is not started by the daemon and is not governed by `knowledge_enabled`, which gates the in-daemon knowledge ingestor and the MCP tools instead.
- **Redaction**: Screen OCR text, clipboard text, voice transcripts, typing bursts and corrections, stored memories, and knowledge ingest are all run through the same pattern filter before they are written to disk, and again before any MCP tool returns them. The patterns cover API keys, AWS keys, GitHub tokens, JWTs, bearer tokens, private key blocks, credit card numbers, SSNs, credential assignments and database connection strings; matches are replaced with a `[REDACTED:CATEGORY]` marker. Redaction is pattern-based, so it catches shapes we know about and cannot be complete. Treat it as a strong default, not a guarantee. As of 0.1.1 nothing carrying a matched pattern reaches either of the two opt-in LLM features above: voice cleanup falls back to rule-based cleanup rather than sending such a dictation, and vocabulary analysis and the consolidator prompt are redacted before they go. Rows written before 0.1.1 are redacted in place by a one-time sweep on the first daemon or MCP-server start after upgrade, covering `activity.db`, `probe.db`, `knowledge.db`, `memory.db` and `memory_cold.db` with their full-text indexes rebuilt; `scripts/purge_clipboard_secrets.py` remains for manual runs.
- **The local MCP endpoint is authenticated**: It requires a per-install bearer token, stored with user-only file permissions, and rejects requests whose `Host` or `Origin` header is not localhost. The stdio transport (`contextpulse-mcp --stdio`) is unauthenticated by design: the client spawns that process itself, as the same user, over a private pipe. The token defends against other local callers, meaning other accounts on the machine, sandboxed applications with loopback access, and other MCP clients and agents. It does not defend against a process already running as you, which can read the token file and the databases directly.
- **File permissions**: The MCP token file is the one thing ContextPulse locks down itself. The databases are not. `activity.db`, `memory.db` and `knowledge.db` are written under your profile directory (`activity.db` under `~/screenshots`) and inherit that directory's operating system permissions; they run in WAL mode, so their `-wal` and `-shm` sidecars appear at runtime with the same inherited ACL. ContextPulse does not set restrictive ACLs on any of them, so on a machine with other user accounts or an unusually permissive profile directory, another local account may be able to read them.

## What redaction does not cover

- Screenshots. Frames are stored as images. The redaction filter reads text, not pixels, so a secret visible on screen is captured in full by `get_screenshot`, `get_recent` and `get_context_at`. The window blocklist is the control for this: name an app there and nothing from it is captured at all. It ships with 14 default patterns (password managers, sign-in and two-factor prompts, Windows Security), and since 0.1.1 a change made in Settings takes effect on the next capture without a restart.
- Window titles. Titles are stored as captured. They are filtered on the way out of an MCP tool, by the same patterns and the same blocklist, but a secret in a document title that matches no pattern is stored and returned as-is.
- Patterns we do not know about. Internal token formats, customer identifiers, medical or legal text, and anything else without a recognizable shape are stored verbatim.
- Anything already on disk, until the sweep has run. Rows written before 0.1.1 are redacted in place on the first start after upgrade, one store at a time, each marked done only after a re-scan finds nothing. A store that was locked or interrupted mid-sweep is retried on the next start rather than skipped. `scripts/purge_clipboard_secrets.py` is still there for a manual run; it is a dry run by default and reports counts by category without printing the values.
- The MCP client. Redaction protects what is written and what is returned. Once your agent has a tool result, where it goes next is that client's decision.
- Who may call the tools, once they hold the token. The MCP server binds to `127.0.0.1:8420`, is not reachable from the network, and now requires a bearer token. Any process running as you can still read that token file and call any of the 37 tools. Redaction limits what such a caller gets back; it does not stop the call.
