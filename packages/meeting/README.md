# ContextPulse Meeting

AI meeting copilot for the ContextPulse platform.

> **Status: Pre-Alpha / Scaffold**
> This package is scaffolded and awaiting implementation. All module methods raise `NotImplementedError` until the spec is provided.

## What It Does

ContextPulse Meeting captures, transcribes, and summarizes meetings in real-time by coordinating the existing Sight (screen capture) and Voice (transcription) modules.

### Planned Features

**Core:**
- Auto-detect meeting apps (Zoom, Teams, Meet, Chime, Slack, WebEx)
- Real-time transcription during meetings (via ContextPulse Voice)
- Screen capture correlation (slides/shared screens matched to transcript)
- Post-meeting AI summary with action items
- Meeting timeline (what was said + what was on screen)

**Nice to Have:**
- Calendar integration for auto-start
- Speaker identification (who said what)
- Rolling summaries during long meetings
- Meeting search across history
- MCP tools for AI agents to query meeting data

## Architecture

```
MeetingDetector ──> MeetingModule ──> MeetingSummarizer
     |                   |                    |
  (watches           (orchestrates        (Claude API
   window             Sight + Voice        summarization)
   titles)             events)
                        |
                  MeetingTimeline
                   (correlates
                    transcript +
                    screenshots)
```

MeetingModule implements ContextPulse's `ModalityModule` interface, so it plugs into the EventBus spine like Sight, Voice, and Touch.

## Platform Support

- **Windows**: Primary target (matches ContextPulse core)
- **macOS**: Supported via platform abstraction layer

## Development

```bash
# From ContextPulse root
uv pip install -e packages/meeting[dev]

# Run tests
pytest packages/meeting/tests/ -v
```

## Rebuilding from Spec

This scaffold is designed to be filled in from a spec document. Run the prompt in the project root's scaffold notes to document your existing implementation, then bring the output here to rebuild each module:

1. `detector.py` — Meeting detection logic
2. `meeting_module.py` — Core orchestration (fill in `NotImplementedError` methods)
3. `summarizer.py` — LLM summarization prompts and logic
4. `timeline.py` — Transcript/screen correlation
5. `mcp_server.py` — MCP tool definitions

## License

AGPL-3.0-or-later. See [LICENSE](../../LICENSE) in the project root.
