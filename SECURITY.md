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

- **Local-first**: All data stays on your machine. No cloud, no telemetry.
- **No network calls**: ContextPulse never phones home or sends data externally.
- **Redaction**: Built-in PII redaction for sensitive content in screen captures.
- **File permissions**: Activity databases are created with user-only access.
