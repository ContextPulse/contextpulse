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

_USING_CONTEXTPULSE_SKILL = """\
---
name: using-contextpulse
description: "ALWAYS invoke this skill when the user says 'look at my screen', 'what's on my screen', 'screenshot', 'read my screen', 'OCR', 'dictation stats', 'voice quality', 'what did I type', 'my activity', or references something visible on-screen. Also invoke when you need visual context for debugging UI issues or verifying results. Do NOT say 'I can't see your screen' — this skill contains the full MCP tool reference for screen capture, voice transcription, input tracking, cross-modal search, and memory."
---

# Using ContextPulse

ContextPulse is a background daemon that captures screen, voice, and input activity. It exposes data through MCP tools.

## Available MCP Tool Groups

### Sight (Screen Capture + OCR)
| Tool | Use When |
|------|----------|
| `get_monitor_summary()` | **Call FIRST** — shows what's on each monitor without images (~50 tokens) |
| `get_screenshot(mode, monitor_index)` | Need to see the screen visually |
| `get_screen_text()` | Need text from screen (cheaper than screenshot) |
| `get_recent(count, seconds, min_diff)` | Recent screen history (min_diff=50 filters to significant changes like app switches) |
| `search_history(query)` | Looking for when something was on screen |
| `get_activity_summary(hours)` | Understanding user's app usage patterns |
| `get_buffer_status()` | Check if daemon is alive and capturing |

**Screenshot modes:**
| Mode | What it captures | When to use |
|------|-----------------|-------------|
| `"active"` (default) | Monitor with cursor | Quick look at what user is focused on |
| `"all"` | Every monitor as separate images | Need full workspace view |
| `"smart"` | Only monitors that changed recently | Save tokens — skip unchanged screens |
| `"monitor"` + `monitor_index=N` | Specific monitor (0-based) | Already know which monitor to check |
| `"region"` | 800x600 around cursor or active window | Focused detail on one area |

**Multi-monitor workflow:**
1. Call `get_monitor_summary()` first — it shows app name, window title, and last-change time per monitor (~50 tokens)
2. Decide which monitor(s) you actually need to see
3. Call `get_screenshot(mode="monitor", monitor_index=N)` for the specific one, or `mode="all"` if you need all

**Choosing the right Sight tool:**
| Need | Tool | Cost |
|------|------|------|
| What's on each screen (text only) | `get_monitor_summary()` | ~50 tokens |
| Read text (code, terminal, docs) | `get_screen_text()` | ~200-700 tokens |
| See all monitors visually | `get_screenshot(mode="all")` | ~1,200 tokens per monitor |
| See one specific monitor | `get_screenshot(mode="monitor", monitor_index=N)` | ~1,200 tokens |
| Only changed monitors | `get_screenshot(mode="smart")` | ~1,200 per changed monitor |
| Daemon health check | `get_buffer_status()` | ~50 tokens |

One monitor often shows a terminal — use `get_monitor_summary()` to identify which, then focus on the OTHER monitor for useful context.

**Daemon restart** (if `get_buffer_status()` returns empty or voice not working):
```bash
# Stop daemon processes only (NOT MCP servers)
# On Windows:
# Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -like '*contextpulse*daemon*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
# On macOS/Linux:
# pkill -f 'contextpulse.*daemon'

# Restart
contextpulse
```
**NEVER** kill all Python processes — this kills MCP servers and other services.
**NEVER** kill processes matching `mcp_unified` — those are the MCP servers agents connect to.

**Hotkeys:** Ctrl+Shift+S (capture), Ctrl+Shift+A (all monitors), Ctrl+Shift+Z (region), Ctrl+Shift+P (pause).

### Voice (Dictation + Vocabulary)
| Tool | Use When |
|------|----------|
| `get_recent_transcriptions(minutes, limit)` | See what user dictated recently (raw + cleaned text) |
| `get_voice_stats(hours)` | Check dictation usage and quality metrics |
| `get_vocabulary(learned_only)` | View current vocabulary corrections |

### Touch (Keyboard + Mouse + Corrections)
| Tool | Use When |
|------|----------|
| `get_recent_touch_events(seconds)` | See typing/clicking activity patterns |
| `get_touch_stats(hours)` | Typing speed, click counts, correction rate |
| `get_correction_history(limit)` | See Voice corrections the user made |

### Cross-Modal Search & History
| Tool | Use When |
|------|----------|
| `search_all_events(query, minutes_ago, modality)` | Search across ALL modalities at once (filter by "sight", "voice", "clipboard", "keys", "flow", "system") |
| `get_event_timeline(minutes_ago, modality)` | Timeline of everything happening at a point in time (optional modality filter) |
| `get_context_at(minutes_ago)` | Get the screen frame + OCR + window title from N minutes ago |
| `get_clipboard_history(count)` | Recent clipboard contents (last N entries) |
| `search_clipboard(query, minutes_ago)` | Search clipboard history by text |

### Learning & Vocabulary
| Tool | Use When |
|------|----------|
| `consolidate_learning(dry_run)` | Run full cross-modal vocabulary consolidation pipeline |
| `check_corrections(hours, threshold, dry_run)` | Check for repeated voice corrections to promote to vocabulary |
| `learn_from_session(hours, dry_run)` | Analyze transcription patterns and learn corrections |
| `rebuild_context_vocabulary()` | Refresh vocabulary from project names |

### Project & Journal
| Tool | Use When |
|------|----------|
| `get_active_project(cwd)` | Detect which project is in focus from CWD |
| `identify_project(text)` | Score arbitrary text against all projects (top 3 matches) |
| `list_projects()` | List all indexed projects with overview and keyword count |
| `get_project_context(project)` | Get full PROJECT_CONTEXT.md for a specific project |
| `route_to_journal(text, entry_type, project)` | Log insight to journal (types: action-discovered, action-completed, observation, decision, context-learned, error-encountered) |

### Memory (Persistent Key-Value Store)
| Tool | Use When |
|------|----------|
| `memory_store(key, value, tags, ttl_hours)` | Store a memory (default 24h TTL, 0 = permanent) |
| `memory_recall(key)` | Retrieve by exact key |
| `memory_search(query, mode)` | Search by keyword, semantic, or hybrid (default) |
| `memory_list(tag, limit)` | List memories, optionally filtered by tag |
| `memory_forget(key)` | Delete a memory |

## Self-Improving Voice

ContextPulse Voice gets smarter over time through multiple learning channels:

1. **Context vocabulary** (automatic): Project names from PROJECT_CONTEXT.md are auto-loaded
2. **Screen correction harvesting** (automatic): After each dictation, checks if screen shows corrected versions
3. **Touch correction detection** (automatic): When user edits Voice-pasted text within 15 seconds
4. **Session learning** (on-demand): `learn_from_session()` finds patterns in transcription history
5. **User vocabulary** (manual): User edits the vocabulary file in the ContextPulse data directory

Run `learn_from_session(hours=4, dry_run=False)` periodically to harvest correction patterns.
After creating new projects, run `rebuild_context_vocabulary()` to update project names.

## What NOT to do

- Don't say "I can't see your screen" — use MCP tools
- Don't ask the user to take a screenshot — one already exists
- Don't kill all Python processes — targeted kill only (match `contextpulse.*daemon`)
- Don't kill processes matching `mcp_unified` — those are shared MCP servers
"""

_ANALYZING_DICTATION_SKILL = """\
---
name: analyzing-dictation
description: "ALWAYS invoke this skill when dictation analysis, speech patterns, vocab learning, transcript analysis, Whisper errors, dictation quality, voice training, analyze my dictation, improve transcription, learn my speech, or wants to make ContextPulse Voice smarter over time. Do NOT run vocabulary learning directly — this skill contains critical safety rules that prevent vocabulary corruption from common-word swaps."
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
For systematic errors not caught by vocabulary, add manually to the
ContextPulse Voice vocabulary file in the data directory.

## Safety Rules (Non-Negotiable)

1. **ALWAYS dry-run first** — never write without reviewing
2. **REJECT common-word swaps** — "task runner" → "TaskRunner" is GOOD; "task" → "TaskRunner" is BAD
3. **Minimum key length** — keys must be 6+ characters
4. **Backup before write** — session_learner.py auto-creates .bak files
5. **User vocab overrides** — never overwrite user's manually set entries

## Recommended Maintenance Cadence

For best results, run dictation analysis periodically:
1. `get_voice_stats(hours=4)` — skip if no dictation this session
2. `learn_from_session(hours=4, dry_run=False)` — learn from recent patterns
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
        import sys
        if sys.platform == "win32":
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" "
                 "| Where-Object { $_.CommandLine -like '*contextpulse*' } "
                 "| Measure-Object | Select-Object -ExpandProperty Count"],
                capture_output=True, text=True, timeout=5,
            )
            count = result.stdout.strip()
        else:
            result = subprocess.run(
                ["pgrep", "-fc", "contextpulse.*daemon"],
                capture_output=True, text=True, timeout=5,
            )
            count = result.stdout.strip()
        if count and int(count) > 0:
            print(f"  Running ({count} process(es))")
        else:
            print("  NOT RUNNING — start with: contextpulse")
    except Exception:
        print("  Could not check daemon status")

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
