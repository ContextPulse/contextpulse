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

- **Local-first**: Capture, storage and search run on your machine. ContextPulse sends none of your data anywhere, and there is no telemetry or analytics.
- **One network call, and you choose whether to make it**: ContextPulse never phones home. The single outbound request it makes on its own is a one-time download of the open-weights embedding model from huggingface.co, and only the first time you use semantic memory search. License verification is offline (Ed25519 signature check, no network).
- **Redaction**: Screen OCR text, clipboard text, voice transcripts, typing bursts and corrections, stored memories, and knowledge ingest are all run through the same pattern filter before they are written to disk, and again before any MCP tool returns them. The patterns cover API keys, AWS keys, GitHub tokens, JWTs, bearer tokens, private key blocks, credit card numbers, SSNs, credential assignments and database connection strings; matches are replaced with a `[REDACTED:CATEGORY]` marker. Redaction is pattern-based, so it catches shapes we know about and cannot be complete. Treat it as a strong default, not a guarantee.
- **File permissions**: Databases are written under your user profile directory and inherit that directory's operating system permissions. ContextPulse does not currently set restrictive ACLs itself, so on a machine with other user accounts or an unusually permissive profile directory, another local account may be able to read them.

## What redaction does not cover

- Screenshots. Frames are stored as images. The redaction filter reads text, not pixels, so a secret visible on screen is captured in full by `get_screenshot`, `get_recent` and `get_context_at`. The window blocklist is the control for this: name an app there and nothing from it is captured at all. The blocklist is read from config when the daemon starts, so a change to it needs a restart.
- Window titles. Titles are stored as captured. They are filtered on the way out of an MCP tool, by the same patterns and the same blocklist, but a secret in a document title that matches no pattern is stored and returned as-is.
- Patterns we do not know about. Internal token formats, customer identifiers, medical or legal text, and anything else without a recognizable shape are stored verbatim.
- Anything already on disk. Rows written before 0.1.1 are still raw in the database. `scripts/purge_clipboard_secrets.py` scrubs them; it runs as a dry run by default and reports counts by category without printing the values.
- The MCP client. Redaction protects what is written and what is returned. Once your agent has a tool result, where it goes next is that client's decision.
- Who may call the tools. The MCP server binds to `127.0.0.1:8420` and is not reachable from the network, but it does not authenticate its callers. Any process running as you on that machine can call any of the 37 tools. Redaction limits what such a caller gets back; it does not stop the call.
