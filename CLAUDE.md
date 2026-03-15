# Project Instructions (Claude)

@C:\Users\david\Projects\AgentConfig\AGENTS.md

## Project-Specific Rules
- This is a monorepo for the ContextPulse platform — always-on context for AI agents
- Python 3.14, packages under `packages/` with separate pyproject.toml each
- **packages/screen** — screen capture daemon + MCP server (most mature)
- **packages/core** — shared config, utilities
- **packages/memory** — cross-session persistent memory (from SynapseAI concepts)
- **packages/agent** — agent coordination, session protocol
- **packages/project** — auto-generated project context
- All captures written to C:\Users\david\screenshots\ (stable paths, overwritten each time)
- Images downscaled to 1280x720 JPEG 85% before storage (~200KB, ~1,229 Claude tokens)
- Privacy-first: window title blocklist, pause hotkey, auto-pause on lock screen
- Reuses patterns from Voiceasy (pynput, pystray, Pillow, python-dotenv)
- Env vars prefixed with CONTEXTPULSE_ (not SCREENCONTEXT_)
