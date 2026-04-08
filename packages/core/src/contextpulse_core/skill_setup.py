# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""ContextPulse companion skill installer for AI coding agents.

ContextPulse ships with companion skills that teach AI agents how to use
its MCP tools effectively. These skills are installed into the agent's
skill directory during `contextpulse --setup`.

Currently supports:
- Claude Code (~/.claude/skills/)
- Gemini CLI (~/.gemini/skills/ or ~/.agent/skills/)

Skills shipped:
- using-contextpulse: Teaches the agent when/how to use Sight, Voice, and Touch MCP tools
- analyzing-dictation: Teaches the agent how to analyze and improve Voice transcription quality
"""

import json
from pathlib import Path

# Skills bundled with ContextPulse, stored as string templates.
# In a real distribution, these would be in a data/ directory.

_USING_CONTEXTPULSE_SKILL = """\
---
name: using-contextpulse
description: "Uses ContextPulse to see the user's screen, hear their dictation, and track their input. TRIGGER when: the user says 'look at my screen', 'what's on my screen', 'screenshot', 'read my screen', 'OCR', 'dictation stats', 'voice quality', 'what did I type', 'my activity', or references something visible on-screen. Also trigger when you need visual context for debugging UI issues or verifying results."
---

# Using ContextPulse

ContextPulse is a background daemon that captures screen, voice, and input activity. It exposes data through MCP tools.

## Available MCP Tool Groups

### Sight (Screen Capture + OCR)
| Tool | Use When |
|------|----------|
| `get_screenshot(mode)` | Need to see the screen (mode: "active", "all", "region") |
| `get_screen_text()` | Need text from screen (cheaper than screenshot) |
| `get_recent(count, seconds)` | Need recent screen history |
| `search_history(query)` | Looking for when something was on screen |
| `get_activity_summary(hours)` | Understanding user's app usage patterns |
| `get_buffer_status()` | Check if daemon is alive and capturing |

**Choosing the right Sight tool:**
| Need | Tool | Cost |
|------|------|------|
| Read text (code, terminal, docs) | `get_screen_text()` | ~200-700 tokens |
| See screen visually | `get_screenshot(mode="all")` | ~1,200 tokens |
| Higher-detail single monitor | `get_screenshot(mode="active")` | ~800 tokens |
| Daemon health check | `get_buffer_status()` | ~50 tokens |

One monitor shows this Claude terminal — focus on the OTHER monitor(s) for useful context.

**Fallback when MCP is unavailable:**
```python
import os, time
from pathlib import Path
p = Path.home() / "screenshots" / "screen_all.png"
age = time.time() - p.stat().st_mtime
print(f"Age: {age:.0f}s - {'FRESH' if age < 30 else 'STALE'}")
```
Default screenshot location: `~/screenshots/screen_all.png` (both monitors), `screen_latest.jpg` (active).

**Daemon restart** (if `get_buffer_status()` returns empty):
```bash
wmic process where "commandline like '%contextpulse_sight%'" call terminate 2>/dev/null
contextpulse-sight &
```
**NEVER** `taskkill /IM pythonw.exe /F` — kills Voice and other Python services too.

**Hotkeys:** Ctrl+Shift+S (capture), Ctrl+Shift+A (all monitors), Ctrl+Shift+Z (region), Ctrl+Shift+P (pause).

### Voice (Dictation + Vocabulary)
| Tool | Use When |
|------|----------|
| `get_recent_transcriptions(minutes)` | See what user dictated recently |
| `get_voice_stats(hours)` | Check dictation usage and quality metrics |
| `get_vocabulary()` | View current vocabulary corrections |
| `learn_from_session(hours, dry_run)` | Analyze transcription patterns and learn corrections |
| `rebuild_context_vocabulary()` | Refresh vocabulary from project names |

### Touch (Keyboard + Mouse + Corrections)
| Tool | Use When |
|------|----------|
| `get_recent_touch_events(seconds)` | See typing/clicking activity patterns |
| `get_touch_stats(hours)` | Typing speed, click counts, correction rate |
| `get_correction_history(limit)` | See Voice corrections the user made |

### Cross-Modal
| Tool | Use When |
|------|----------|
| `search_all_events(query)` | Search across ALL modalities at once |
| `get_event_timeline(minutes_ago)` | See everything happening at a point in time |
| `get_clipboard_history(count)` | Recent clipboard contents |

## Self-Improving Voice

ContextPulse Voice gets smarter over time through multiple learning channels:

1. **Context vocabulary** (automatic): Project names from PROJECT_CONTEXT.md are auto-loaded
2. **Screen correction harvesting** (automatic): After each dictation, checks if screen shows corrected versions
3. **Touch correction detection** (automatic): When user edits Voice-pasted text within 15 seconds
4. **Session learning** (end-of-session): `learn_from_session()` finds patterns in transcription history
5. **User vocabulary** (manual): User edits `%APPDATA%/ContextPulse/voice/vocabulary.json`

At end of session, run `learn_from_session(hours=4, dry_run=False)` to harvest patterns.
After creating new projects, run `rebuild_context_vocabulary()` to update project names.

## What NOT to do

- Don't say "I can't see your screen" — use MCP tools or file fallback
- Don't ask the user to take a screenshot — one already exists
- Don't kill all pythonw.exe — targeted kill only
"""

_ANALYZING_DICTATION_SKILL = """\
---
name: analyzing-dictation
description: "Analyzes ContextPulse Voice dictation history to discover speech patterns, Whisper failure modes, and auto-generate vocabulary corrections. Use when the user mentions dictation analysis, speech patterns, vocab learning, transcript analysis, Whisper errors, dictation quality, voice training, analyze my dictation, improve transcription, learn my speech, or wants to make ContextPulse Voice smarter over time. Also trigger on nightly/weekly maintenance."
---

# Analyzing Dictation Patterns (ContextPulse Voice)

## Purpose

Analyze ContextPulse Voice transcription history to discover patterns that improve transcription quality
over time. Three learning layers work automatically:

1. **Context vocabulary** (`vocabulary_context.json`) — project names auto-extracted from PROJECT_CONTEXT.md
2. **Learned vocabulary** (`vocabulary_learned.json`) — corrections found by session analysis and OCR harvesting
3. **Screen-aware LLM cleanup** — uses recent window titles for proper noun context hints

## MCP Tools

| Tool | Purpose |
|------|---------|
| `learn_from_session(hours=24, dry_run=True)` | Find learnable patterns in transcription history |
| `rebuild_context_vocabulary()` | Regenerate context vocab from project directories |
| `get_voice_stats(hours=24)` | Dictation count, duration, correction rate |
| `get_recent_transcriptions(minutes=60)` | View raw vs cleaned transcripts |
| `get_vocabulary(learned_only=False)` | View current vocabulary entries |

## Analysis Workflow

### Step 1: Check current state
```
get_voice_stats(hours=168)          # Last week
get_vocabulary(learned_only=True)   # What's been learned
```

### Step 2: Dry-run session learning
```
learn_from_session(hours=168, dry_run=True)
```
Review output. **CRITICAL: reject any entry that swaps a common English word for another common word**
(e.g., "cause" → "because"). These corrupt all future dictation.

### Step 3: Apply if safe
```
learn_from_session(hours=168, dry_run=False)
rebuild_context_vocabulary()
```

### Step 4: Review examples
```
get_recent_transcriptions(minutes=1440)  # Last 24 hours
```
For systematic errors not caught by vocabulary, add manually to
`%APPDATA%/ContextPulse/voice/vocabulary.json`.

## Safety Rules (Non-Negotiable)

1. **ALWAYS dry-run first** — never write without reviewing
2. **REJECT common-word swaps** — "task runner" → "TaskRunner" is GOOD; "task" → "TaskRunner" is BAD
3. **Minimum key length** — keys must be 6+ characters
4. **Backup before write** — session_learner.py auto-creates .bak files
5. **User vocab overrides** — never overwrite user's manually set entries

## Integration with /end-session

Step 12 of `/end-session` runs automatically:
1. `get_voice_stats(hours=4)` — skip if no dictation this session
2. `learn_from_session(hours=4, dry_run=False)` — learn from this session's patterns
3. `rebuild_context_vocabulary()` — refresh if projects changed

## Common Whisper Failure Patterns

**Fixable by vocabulary:** CamelCase splitting ("context pulse" → "ContextPulse"), technical jargon ("cube control" → "kubectl"), product names.

**Fixable by LLM cleanup:** Context-dependent corrections, homophones (their/there), sentence restructuring.

**Not fixable:** Accent-specific errors, very short utterances, heavy background noise.
"""

_SKILL_CONFIGS = {
    "using-contextpulse": {
        "content": _USING_CONTEXTPULSE_SKILL,
        "filename": "SKILL.md",
    },
    "analyzing-dictation": {
        "content": _ANALYZING_DICTATION_SKILL,
        "filename": "SKILL.md",
    },
}

# Agent skill directories
_AGENT_SKILL_DIRS = {
    "claude-code": Path.home() / ".claude" / "skills",
    "gemini": Path.home() / ".gemini" / "skills",
}


def install_skills(agent: str = "claude-code", force: bool = False) -> list[str]:
    """Install ContextPulse companion skills for the specified agent.

    Args:
        agent: "claude-code" or "gemini"
        force: Overwrite existing skills if True

    Returns:
        List of installed skill names.
    """
    if agent not in _AGENT_SKILL_DIRS:
        print(f"Unknown agent: {agent}. Supported: {', '.join(_AGENT_SKILL_DIRS)}")
        return []

    skills_dir = _AGENT_SKILL_DIRS[agent]
    installed = []

    for skill_name, config in _SKILL_CONFIGS.items():
        skill_dir = skills_dir / skill_name
        skill_file = skill_dir / config["filename"]

        if skill_file.exists() and not force:
            print(f"  Skill '{skill_name}' already installed at {skill_dir}")
            print("  Use --force to overwrite")
            continue

        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(config["content"], encoding="utf-8")
        print(f"  Installed skill '{skill_name}' -> {skill_dir}")
        installed.append(skill_name)

    return installed


def print_ecosystem_status() -> None:
    """Print the current ContextPulse ecosystem setup status."""
    print("\n=== ContextPulse Ecosystem Status ===\n")

    # Check daemon
    print("Daemon:")
    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" "
             "| Where-Object { $_.CommandLine -like '*contextpulse*' } "
             "| Measure-Object | Select-Object -ExpandProperty Count"],
            capture_output=True, text=True, timeout=5,
        )
        count = result.stdout.strip()
        if count and int(count) > 0:
            print(f"  Running ({count} process(es))")
        else:
            print("  NOT RUNNING — start with: contextpulse")
    except Exception:
        print("  Could not check (non-Windows or error)")

    # Check MCP servers
    print("\nMCP Servers:")
    claude_config = Path.home() / ".claude.json"
    if claude_config.exists():
        try:
            config = json.loads(claude_config.read_text(encoding="utf-8"))
            servers = config.get("mcpServers", {})
            for name in ["contextpulse-sight", "contextpulse-voice", "contextpulse-touch",
                         "contextpulse-project"]:
                status = "configured" if name in servers else "NOT configured"
                print(f"  {name}: {status}")
        except Exception:
            print("  Could not read ~/.claude.json")
    else:
        print("  ~/.claude.json not found")

    # Check companion skills
    print("\nCompanion Skills:")
    for agent, base_dir in _AGENT_SKILL_DIRS.items():
        for skill_name in _SKILL_CONFIGS:
            skill_file = base_dir / skill_name / "SKILL.md"
            status = "installed" if skill_file.exists() else "NOT installed"
            print(f"  [{agent}] {skill_name}: {status}")

    # Check vocabulary
    print("\nVocabulary:")
    from contextpulse_voice.config import CONTEXT_VOCAB_FILE, LEARNED_VOCAB_FILE, VOCAB_FILE
    for name, path in [("User", VOCAB_FILE), ("Learned", LEARNED_VOCAB_FILE),
                       ("Context", CONTEXT_VOCAB_FILE)]:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                print(f"  {name}: {len(data)} entries ({path.name})")
            except Exception:
                print(f"  {name}: exists but unreadable ({path.name})")
        else:
            print(f"  {name}: not yet created ({path.name})")

    print()
