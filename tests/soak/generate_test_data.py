"""Generate realistic multi-modal activity data for soak testing ContextPulse Pro features.

Writes directly to the EventBus (same DB APIs the real daemon uses) so the
Pro tools (search_all_events, get_event_timeline) see it immediately.

All generated events carry a payload marker {"_soak_test": True} so they can
be cleaned up without touching real data.

Usage:
    python -m tests.soak.generate_test_data --hours 24 --start-time "2026-03-26T08:00:00"
    python -m tests.soak.generate_test_data --hours 8   # last 8 hours ending now
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import random
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure the packages are importable (editable install or sys.path)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _pkg in ("core", "screen", "voice", "touch"):
    _src = _PROJECT_ROOT / "packages" / _pkg / "src"
    if _src.exists() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

from contextpulse_core.spine import ContextEvent, EventBus, EventType, Modality

logger = logging.getLogger(__name__)

# ── Soak marker ──────────────────────────────────────────────────────────
SOAK_MARKER_KEY = "_soak_test"
SOAK_MARKER = {SOAK_MARKER_KEY: True}


def _uid() -> str:
    return uuid.uuid4().hex[:16]


# ═══════════════════════════════════════════════════════════════════════════
# Realistic content pools
# ═══════════════════════════════════════════════════════════════════════════

APPS_AND_TITLES = {
    "Code.exe": [
        "main.py - ContextPulse - Visual Studio Code",
        "bus.py - contextpulse_core - Visual Studio Code",
        "test_spine.py - tests - Visual Studio Code",
        "settings.json - .vscode - Visual Studio Code",
        "README.md - ContextPulse - Visual Studio Code",
        "daemon.py - contextpulse_core - Visual Studio Code",
        "mcp_server.py - contextpulse_sight - Visual Studio Code",
        "voice_module.py - contextpulse_voice - Visual Studio Code",
        "Untitled-1 - Visual Studio Code",
        "requirements.txt - ContextPulse - Visual Studio Code",
    ],
    "chrome.exe": [
        "GitHub - ContextPulse - Pull requests - Google Chrome",
        "Stack Overflow - python sqlite fts5 trigger - Google Chrome",
        "Claude - Anthropic - Google Chrome",
        "Gmail - Inbox (3) - Google Chrome",
        "Google Docs - Project Plan Q2 - Google Chrome",
        "AWS Console - Lambda Functions - Google Chrome",
        "Jira - CPULSE-142 - Fix OCR timeout - Google Chrome",
        "YouTube - FastAPI tutorial - Google Chrome",
        "Reddit - r/Python - Google Chrome",
        "MDN Web Docs - EventTarget - Google Chrome",
        "PyPI - contextpulse 0.1.0 - Google Chrome",
        "Notion - Sprint Backlog - Google Chrome",
    ],
    "WindowsTerminal.exe": [
        "PowerShell - C:\\Users\\david\\Projects\\ContextPulse",
        "PowerShell - pytest -x packages/core",
        "PowerShell - git log --oneline",
        "PowerShell - pip install -e .",
        "PowerShell - python -m contextpulse_core.daemon",
        "cmd.exe - build.cmd",
    ],
    "Slack.exe": [
        "Slack - #contextpulse-dev - Jerard Ventures",
        "Slack - #general - Jerard Ventures",
        "Slack - DM: Chris Jerard",
        "Slack - #random",
    ],
    "explorer.exe": [
        "ContextPulse - File Explorer",
        "Downloads - File Explorer",
        "screenshots - File Explorer",
    ],
    "Obsidian.exe": [
        "Daily Note - 2026-03-26 - Obsidian",
        "ContextPulse Architecture - Obsidian",
        "Meeting Notes - Obsidian",
    ],
}

OCR_CODE_SNIPPETS = [
    "def emit(self, event: ContextEvent) -> None:\n    if not event.validate():\n        raise ValueError(f'Invalid event: {event.event_id}')\n    row = event.to_row()",
    "import sqlite3\nconn = sqlite3.connect('activity.db')\ncursor = conn.execute('SELECT * FROM events WHERE timestamp > ?', (cutoff,))",
    "class EventBus:\n    def __init__(self, db_path):\n        self._db_path = Path(db_path)\n        self._lock = threading.Lock()",
    "async def handle_request(request):\n    data = await request.json()\n    result = process_event(data)\n    return JSONResponse(result)",
    "PRAGMA journal_mode=WAL;\nPRAGMA synchronous=NORMAL;\nCREATE TABLE IF NOT EXISTS events (\n    event_id TEXT PRIMARY KEY,\n    timestamp REAL NOT NULL\n);",
    "pytest packages/core/tests/test_spine.py -v\n\nPASSED test_emit_event\nPASSED test_query_recent\nPASSED test_search_fts\n3 passed in 0.42s",
    "git status\nOn branch feature/soak-tests\nChanges staged for commit:\n  new file: tests/soak/generate_test_data.py",
    "ERROR: ConnectionRefusedError: [WinError 10061]\nTraceback (most recent call last):\n  File 'daemon.py', line 45, in start\n    self._connect()",
    "pip install contextpulse-core==0.1.0\nCollecting contextpulse-core\n  Downloading contextpulse_core-0.1.0-py3-none-any.whl (42 kB)",
    "from contextpulse_core.spine import ContextEvent, Modality, EventType\nevent = ContextEvent(\n    modality=Modality.SIGHT,\n    event_type=EventType.SCREEN_CAPTURE,\n)",
]

OCR_BROWSER_SNIPPETS = [
    "Pull Request #142 - Fix OCR timeout on large screens\nBase: main <- feature/ocr-timeout\n+12 -3 files changed\nAll checks passed",
    "python sqlite3 fts5 trigger not firing\nAsked 2 hours ago  Active  Modified\n3 Answers  Sorted by: Highest score",
    "Claude: I'll help you implement the cross-modal search feature.\nLet me review the EventBus code first...",
    "Inbox (3)\nFrom: AWS Notifications\nSubject: Lambda function ContextPulse-license exceeded duration limit",
    "Sprint Backlog - Week 13\n[ ] CPULSE-140 Soak test suite\n[x] CPULSE-139 FTS5 triggers\n[ ] CPULSE-141 Voice cleanup improvements",
    "Product Hunt - ContextPulse: Always-on context for AI agents\n142 upvotes  23 comments  #3 Product of the Day",
    "Stack Overflow - How to use FTS5 with content sync triggers in SQLite\nAnswer: You need to create AFTER INSERT and AFTER DELETE triggers...",
    "AWS Lambda > Functions > contextpulse-license-verify\nRuntime: Python 3.12  Memory: 128 MB  Timeout: 30 sec\nLast invocation: 2 minutes ago",
]

OCR_CHAT_SNIPPETS = [
    "Chris: Hey did you see the new capture metrics? Looking good\nDavid: Yeah the smart mode is saving about 60% disk writes\nChris: Nice, lets discuss at the Thursday sync",
    "David: @team the v0.1.0 build is ready for testing\nAlice: On it! Running the installer now\nBob: I'll test the MCP tools with Claude",
    "Chris: The correction detector caught 15 voice typos today\nDavid: That's the self-improving vocabulary working\nChris: We should add that to the launch blog post",
]

OCR_DOCS_SNIPPETS = [
    "ContextPulse Architecture\n\nThe EventBus is the central nervous system. Every modality module emits\nContextEvent objects that get persisted to the events table with FTS5 indexing.",
    "Meeting Notes - March 26\n\nAgenda:\n1. Review soak test results\n2. Discuss Pro pricing tiers\n3. Plan beta launch timeline\n\nAction items: David to build data generator",
    "Daily Note\n\n- Fixed the FTS5 trigger issue (was missing COALESCE on payload extraction)\n- Voice module now emits SPEECH_START/END events correctly\n- Touch module correction detection working across app switches",
]

VOICE_TRANSCRIPTS = [
    "deploy the authentication fix to staging",
    "hey Claude can you search for the error message in the logs",
    "remind me to review the pull request before the meeting",
    "let me check the event timeline for the last five minutes",
    "the voice transcription accuracy looks much better today",
    "we need to add more test coverage for the cross modal search",
    "open the context pulse project in VS Code",
    "run the soak tests and check the performance numbers",
    "the clipboard monitor is capturing too many short snippets",
    "schedule a meeting with Chris to discuss the beta launch",
    "note to self the FTS5 tokenizer should use porter stemming",
    "search for all events mentioning the deploy error",
    "let me dictate the release notes for version zero point one",
    "the screen capture interval should be configurable per app",
    "take a screenshot of the test results dashboard",
    "commit the changes with message fix OCR timeout handling",
    "check the activity summary for the last eight hours",
    "the correction detector needs a longer watch window",
    "send a message to the team channel about the new build",
    "switch to the terminal and run pytest with verbose output",
]

CLIPBOARD_SNIPPETS = [
    "ConnectionRefusedError: [WinError 10061] No connection could be made",
    "https://github.com/jerardventures/contextpulse/pull/142",
    "def search(self, query: str, minutes_ago: float = 30) -> list[dict]:",
    "pip install contextpulse==0.1.0",
    "SELECT e.*, rank FROM events_fts fts JOIN events e ON e.rowid = fts.rowid WHERE events_fts MATCH ?",
    "CPULSE-142: Fix OCR timeout on large multi-monitor setups",
    "https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.execute",
    "from contextpulse_core.spine import ContextEvent, EventBus, Modality",
    "git checkout -b feature/soak-tests",
    '{"event_id": "abc123", "modality": "sight", "event_type": "screen_capture"}',
    "https://contextpulse.ai/docs/getting-started",
    "RuntimeError: Whisper model failed to load: CUDA out of memory",
    "pytest packages/ -x -v --tb=short 2>&1 | head -50",
    "ANTHROPIC_API_KEY=<REDACTED>",
    "docker build -t contextpulse:latest .",
    "The FTS5 trigger needs to use COALESCE to handle null payloads",
]


# ═══════════════════════════════════════════════════════════════════════════
# Data generators — one per modality
# ═══════════════════════════════════════════════════════════════════════════

def _random_app() -> tuple[str, str]:
    """Return (app_name, window_title)."""
    app = random.choice(list(APPS_AND_TITLES.keys()))
    title = random.choice(APPS_AND_TITLES[app])
    return app, title


def _jitter(base: float, pct: float = 0.3) -> float:
    """Add random jitter to a base interval."""
    return base * (1 + random.uniform(-pct, pct))


def generate_screen_events(
    start_ts: float,
    end_ts: float,
    interval: float = 7.0,
) -> list[ContextEvent]:
    """Generate screen capture + OCR events at ~5-10s intervals.

    Each capture produces a SCREEN_CAPTURE event, and ~80% also get an
    OCR_RESULT event 0.5-2s later (simulating async OCR processing).
    """
    events = []
    ts = start_ts
    last_app = ""

    while ts < end_ts:
        app, title = _random_app()

        # Emit window focus change ~30% of the time
        if app != last_app and random.random() < 0.3:
            events.append(ContextEvent(
                event_id=_uid(),
                timestamp=ts - 0.1,
                modality=Modality.SYSTEM,
                event_type=EventType.WINDOW_FOCUS,
                app_name=app,
                window_title=title,
                payload={**SOAK_MARKER},
            ))
            last_app = app

        # Screen capture
        diff_score = random.uniform(0.01, 0.95)
        events.append(ContextEvent(
            event_id=_uid(),
            timestamp=ts,
            modality=Modality.SIGHT,
            event_type=EventType.SCREEN_CAPTURE,
            app_name=app,
            window_title=title,
            monitor_index=random.choice([0, 0, 0, 1]),  # mostly primary
            payload={
                **SOAK_MARKER,
                "frame_path": f"screenshots/soak_{int(ts)}.jpg",
                "diff_score": diff_score,
                "token_estimate": random.randint(300, 1500),
                "storage_mode": "smart",
            },
        ))

        # OCR result ~80% of captures, 0.5-2s later
        if random.random() < 0.80:
            ocr_delay = random.uniform(0.5, 2.0)
            # Choose OCR text based on app
            if "Code.exe" in app:
                ocr_text = random.choice(OCR_CODE_SNIPPETS)
            elif "chrome.exe" in app:
                ocr_text = random.choice(OCR_BROWSER_SNIPPETS)
            elif "Slack.exe" in app:
                ocr_text = random.choice(OCR_CHAT_SNIPPETS)
            elif "Obsidian.exe" in app:
                ocr_text = random.choice(OCR_DOCS_SNIPPETS)
            else:
                ocr_text = random.choice(OCR_CODE_SNIPPETS + OCR_BROWSER_SNIPPETS)

            events.append(ContextEvent(
                event_id=_uid(),
                timestamp=ts + ocr_delay,
                modality=Modality.SIGHT,
                event_type=EventType.OCR_RESULT,
                app_name=app,
                window_title=title,
                monitor_index=0,
                payload={
                    **SOAK_MARKER,
                    "ocr_text": ocr_text,
                    "ocr_confidence": random.uniform(0.7, 0.99),
                    "frame_path": f"screenshots/soak_{int(ts)}.jpg",
                },
            ))

        ts += _jitter(interval)

    return events


def generate_voice_events(
    start_ts: float,
    end_ts: float,
    events_per_hour: int = 10,
) -> list[ContextEvent]:
    """Generate voice dictation events (SPEECH_START, SPEECH_END, TRANSCRIPTION).

    Each dictation session is a triplet: start -> end (1-8s later) -> transcription.
    """
    events = []
    duration = end_ts - start_ts
    total = max(1, int((duration / 3600) * events_per_hour))
    interval = duration / total

    ts = start_ts + random.uniform(30, 120)  # first dictation after a short delay

    for _ in range(total):
        if ts >= end_ts:
            break

        app, title = _random_app()
        transcript = random.choice(VOICE_TRANSCRIPTS)
        recording_duration = random.uniform(1.0, 8.0)
        correlation = _uid()

        # SPEECH_START
        events.append(ContextEvent(
            event_id=_uid(),
            timestamp=ts,
            modality=Modality.VOICE,
            event_type=EventType.SPEECH_START,
            app_name=app,
            window_title=title,
            correlation_id=correlation,
            payload={**SOAK_MARKER},
        ))

        # SPEECH_END
        end_time = ts + recording_duration
        events.append(ContextEvent(
            event_id=_uid(),
            timestamp=end_time,
            modality=Modality.VOICE,
            event_type=EventType.SPEECH_END,
            app_name=app,
            window_title=title,
            correlation_id=correlation,
            payload={
                **SOAK_MARKER,
                "duration_seconds": recording_duration,
            },
        ))

        # TRANSCRIPTION (0.5-3s processing delay)
        transcribe_time = end_time + random.uniform(0.5, 3.0)
        events.append(ContextEvent(
            event_id=_uid(),
            timestamp=transcribe_time,
            modality=Modality.VOICE,
            event_type=EventType.TRANSCRIPTION,
            app_name=app,
            window_title=title,
            correlation_id=correlation,
            payload={
                **SOAK_MARKER,
                "transcript": transcript,
                "raw_transcript": transcript.lower().replace(",", "").replace(".", ""),
                "confidence": random.uniform(0.75, 0.98),
                "duration_seconds": recording_duration,
                "word_count": len(transcript.split()),
            },
        ))

        ts += _jitter(interval)

    return events


def generate_typing_events(
    start_ts: float,
    end_ts: float,
    bursts_per_hour: int = 60,
) -> list[ContextEvent]:
    """Generate typing burst events with realistic WPM and word counts."""
    events = []
    duration = end_ts - start_ts
    total = max(1, int((duration / 3600) * bursts_per_hour))
    interval = duration / total

    ts = start_ts + random.uniform(5, 30)

    for _ in range(total):
        if ts >= end_ts:
            break

        app, title = _random_app()
        wpm = random.gauss(65, 20)
        wpm = max(15, min(140, wpm))
        word_count = random.randint(3, 40)
        char_count = int(word_count * 5.2)
        backspace_count = random.randint(0, max(1, int(char_count * 0.08)))
        burst_duration = (word_count / wpm) * 60

        events.append(ContextEvent(
            event_id=_uid(),
            timestamp=ts,
            modality=Modality.KEYS,
            event_type=EventType.TYPING_BURST,
            app_name=app,
            window_title=title,
            payload={
                **SOAK_MARKER,
                "word_count": word_count,
                "char_count": char_count,
                "backspace_count": backspace_count,
                "wpm": round(wpm, 1),
                "duration_seconds": round(burst_duration, 2),
                "backspace_ratio": round(backspace_count / max(1, char_count), 3),
            },
        ))

        ts += _jitter(interval)

    return events


def generate_mouse_events(
    start_ts: float,
    end_ts: float,
    events_per_hour: int = 200,
) -> list[ContextEvent]:
    """Generate click and scroll events with realistic patterns."""
    events = []
    duration = end_ts - start_ts
    total = max(1, int((duration / 3600) * events_per_hour))
    interval = duration / total

    ts = start_ts + random.uniform(2, 10)

    for _ in range(total):
        if ts >= end_ts:
            break

        app, title = _random_app()

        # 40% clicks, 55% scrolls, 5% drags
        r = random.random()
        if r < 0.40:
            events.append(ContextEvent(
                event_id=_uid(),
                timestamp=ts,
                modality=Modality.FLOW,
                event_type=EventType.CLICK,
                app_name=app,
                window_title=title,
                payload={
                    **SOAK_MARKER,
                    "x": random.randint(50, 2500),
                    "y": random.randint(50, 1400),
                    "button": random.choice(["left", "left", "left", "right", "middle"]),
                    "click_type": random.choice(["single", "single", "single", "double"]),
                },
            ))
        elif r < 0.95:
            events.append(ContextEvent(
                event_id=_uid(),
                timestamp=ts,
                modality=Modality.FLOW,
                event_type=EventType.SCROLL,
                app_name=app,
                window_title=title,
                payload={
                    **SOAK_MARKER,
                    "x": random.randint(200, 2000),
                    "y": random.randint(200, 1200),
                    "dx": 0,
                    "dy": random.choice([-3, -2, -1, 1, 2, 3]),
                },
            ))
        else:
            events.append(ContextEvent(
                event_id=_uid(),
                timestamp=ts,
                modality=Modality.FLOW,
                event_type=EventType.DRAG,
                app_name=app,
                window_title=title,
                payload={
                    **SOAK_MARKER,
                    "start_x": random.randint(100, 1500),
                    "start_y": random.randint(100, 900),
                    "end_x": random.randint(100, 1500),
                    "end_y": random.randint(100, 900),
                    "duration_ms": random.randint(200, 2000),
                },
            ))

        ts += _jitter(interval)

    return events


def generate_clipboard_events(
    start_ts: float,
    end_ts: float,
    events_per_hour: int = 15,
) -> list[ContextEvent]:
    """Generate clipboard change events with realistic developer content."""
    events = []
    duration = end_ts - start_ts
    total = max(1, int((duration / 3600) * events_per_hour))
    interval = duration / total

    ts = start_ts + random.uniform(20, 90)

    for _ in range(total):
        if ts >= end_ts:
            break

        text = random.choice(CLIPBOARD_SNIPPETS)
        app, title = _random_app()
        text_hash = hashlib.md5(text.encode()).hexdigest()[:12]

        events.append(ContextEvent(
            event_id=_uid(),
            timestamp=ts,
            modality=Modality.CLIPBOARD,
            event_type=EventType.CLIPBOARD_CHANGE,
            app_name=app,
            window_title=title,
            payload={
                **SOAK_MARKER,
                "text": text,
                "hash": text_hash,
                "source_app": app,
            },
        ))

        # ~30% of clipboard copies are followed by a PASTE_DETECTED 1-5s later
        if random.random() < 0.30:
            paste_delay = random.uniform(1.0, 5.0)
            paste_app, paste_title = _random_app()
            events.append(ContextEvent(
                event_id=_uid(),
                timestamp=ts + paste_delay,
                modality=Modality.KEYS,
                event_type=EventType.PASTE_DETECTED,
                app_name=paste_app,
                window_title=paste_title,
                payload={
                    **SOAK_MARKER,
                    "text": text[:200],
                    "source_app": app,
                    "target_app": paste_app,
                },
            ))

        ts += _jitter(interval)

    return events


def generate_correlated_sequences(
    start_ts: float,
    end_ts: float,
    sequences_per_hour: int = 5,
) -> list[ContextEvent]:
    """Generate correlated multi-modal event sequences.

    Each sequence simulates a realistic workflow like:
    - Typing code -> copy snippet -> paste in terminal -> voice note
    - Reading docs -> copy URL -> switch to Slack -> paste + type message
    """
    events = []
    duration = end_ts - start_ts
    total = max(1, int((duration / 3600) * sequences_per_hour))
    interval = duration / total

    ts = start_ts + random.uniform(60, 300)

    WORKFLOWS = [
        _workflow_code_and_test,
        _workflow_browse_and_share,
        _workflow_voice_and_fix,
        _workflow_debug_cycle,
    ]

    for _ in range(total):
        if ts >= end_ts:
            break
        workflow = random.choice(WORKFLOWS)
        seq_events = workflow(ts)
        events.extend(seq_events)
        ts += _jitter(interval)

    return events


def _workflow_code_and_test(ts: float) -> list[ContextEvent]:
    """Typing in VS Code -> copy code -> run test in terminal."""
    cid = _uid()
    code = random.choice(OCR_CODE_SNIPPETS)
    return [
        ContextEvent(
            event_id=_uid(), timestamp=ts,
            modality=Modality.KEYS, event_type=EventType.TYPING_BURST,
            app_name="Code.exe", window_title="bus.py - contextpulse_core - Visual Studio Code",
            correlation_id=cid,
            payload={**SOAK_MARKER, "word_count": 15, "wpm": 72.0, "char_count": 78},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 3.0,
            modality=Modality.CLIPBOARD, event_type=EventType.CLIPBOARD_CHANGE,
            app_name="Code.exe", window_title="bus.py - contextpulse_core - Visual Studio Code",
            correlation_id=cid,
            payload={**SOAK_MARKER, "text": code[:100], "hash": hashlib.md5(code.encode()).hexdigest()[:12]},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 5.0,
            modality=Modality.SYSTEM, event_type=EventType.WINDOW_FOCUS,
            app_name="WindowsTerminal.exe", window_title="PowerShell - pytest -x packages/core",
            correlation_id=cid,
            payload={**SOAK_MARKER},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 6.0,
            modality=Modality.KEYS, event_type=EventType.PASTE_DETECTED,
            app_name="WindowsTerminal.exe", window_title="PowerShell - pytest -x packages/core",
            correlation_id=cid,
            payload={**SOAK_MARKER, "text": "pytest -x packages/core", "source_app": "Code.exe"},
        ),
    ]


def _workflow_browse_and_share(ts: float) -> list[ContextEvent]:
    """Browse docs -> copy URL -> paste in Slack."""
    cid = _uid()
    url = "https://docs.python.org/3/library/sqlite3.html"
    return [
        ContextEvent(
            event_id=_uid(), timestamp=ts,
            modality=Modality.SIGHT, event_type=EventType.OCR_RESULT,
            app_name="chrome.exe", window_title="SQLite3 - Python Docs - Google Chrome",
            correlation_id=cid,
            payload={**SOAK_MARKER, "ocr_text": "sqlite3 — DB-API 2.0 interface for SQLite databases"},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 2.0,
            modality=Modality.CLIPBOARD, event_type=EventType.CLIPBOARD_CHANGE,
            app_name="chrome.exe", window_title="SQLite3 - Python Docs - Google Chrome",
            correlation_id=cid,
            payload={**SOAK_MARKER, "text": url, "hash": hashlib.md5(url.encode()).hexdigest()[:12]},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 4.0,
            modality=Modality.SYSTEM, event_type=EventType.WINDOW_FOCUS,
            app_name="Slack.exe", window_title="Slack - #contextpulse-dev - Jerard Ventures",
            correlation_id=cid,
            payload={**SOAK_MARKER},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 5.0,
            modality=Modality.KEYS, event_type=EventType.PASTE_DETECTED,
            app_name="Slack.exe", window_title="Slack - #contextpulse-dev - Jerard Ventures",
            correlation_id=cid,
            payload={**SOAK_MARKER, "text": url, "source_app": "chrome.exe"},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 6.0,
            modality=Modality.KEYS, event_type=EventType.TYPING_BURST,
            app_name="Slack.exe", window_title="Slack - #contextpulse-dev - Jerard Ventures",
            correlation_id=cid,
            payload={**SOAK_MARKER, "word_count": 8, "wpm": 55.0, "text": "check out this sqlite3 docs page"},
        ),
    ]


def _workflow_voice_and_fix(ts: float) -> list[ContextEvent]:
    """Voice dictation -> correction detected -> typing fix."""
    cid = _uid()
    transcript = "deploy the authentication fix"
    return [
        ContextEvent(
            event_id=_uid(), timestamp=ts,
            modality=Modality.VOICE, event_type=EventType.SPEECH_START,
            app_name="Code.exe", window_title="main.py - ContextPulse - Visual Studio Code",
            correlation_id=cid,
            payload={**SOAK_MARKER},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 3.0,
            modality=Modality.VOICE, event_type=EventType.SPEECH_END,
            app_name="Code.exe", window_title="main.py - ContextPulse - Visual Studio Code",
            correlation_id=cid,
            payload={**SOAK_MARKER, "duration_seconds": 3.0},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 4.5,
            modality=Modality.VOICE, event_type=EventType.TRANSCRIPTION,
            app_name="Code.exe", window_title="main.py - ContextPulse - Visual Studio Code",
            correlation_id=cid,
            payload={**SOAK_MARKER, "transcript": transcript, "raw_transcript": "deploy the authentification fix", "confidence": 0.82},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 7.0,
            modality=Modality.KEYS, event_type=EventType.CORRECTION_DETECTED,
            app_name="Code.exe", window_title="main.py - ContextPulse - Visual Studio Code",
            correlation_id=cid,
            payload={
                **SOAK_MARKER,
                "original_text": "authentification",
                "corrected_text": "authentication",
                "correction_text": "authentification -> authentication",
                "correction_type": "spelling",
                "confidence": 0.9,
            },
        ),
    ]


def _workflow_debug_cycle(ts: float) -> list[ContextEvent]:
    """See error on screen -> copy error -> search for it -> voice note."""
    cid = _uid()
    error = "ConnectionRefusedError: [WinError 10061] No connection could be made"
    return [
        ContextEvent(
            event_id=_uid(), timestamp=ts,
            modality=Modality.SIGHT, event_type=EventType.OCR_RESULT,
            app_name="WindowsTerminal.exe", window_title="PowerShell - python -m contextpulse_core.daemon",
            correlation_id=cid,
            payload={**SOAK_MARKER, "ocr_text": f"ERROR: {error}\nTraceback (most recent call last):\n  File 'daemon.py', line 45"},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 2.0,
            modality=Modality.CLIPBOARD, event_type=EventType.CLIPBOARD_CHANGE,
            app_name="WindowsTerminal.exe", window_title="PowerShell - python -m contextpulse_core.daemon",
            correlation_id=cid,
            payload={**SOAK_MARKER, "text": error, "hash": hashlib.md5(error.encode()).hexdigest()[:12]},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 4.0,
            modality=Modality.SYSTEM, event_type=EventType.WINDOW_FOCUS,
            app_name="chrome.exe", window_title="Stack Overflow - python ConnectionRefusedError - Google Chrome",
            correlation_id=cid,
            payload={**SOAK_MARKER},
        ),
        ContextEvent(
            event_id=_uid(), timestamp=ts + 8.0,
            modality=Modality.VOICE, event_type=EventType.TRANSCRIPTION,
            app_name="chrome.exe", window_title="Stack Overflow - python ConnectionRefusedError - Google Chrome",
            correlation_id=cid,
            payload={**SOAK_MARKER, "transcript": "the daemon connection refused error is probably a port conflict", "confidence": 0.88},
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Main orchestrator
# ═══════════════════════════════════════════════════════════════════════════

def generate_all(
    db_path: Path | str,
    hours: float = 24.0,
    start_time: datetime | None = None,
) -> dict[str, int]:
    """Generate a full workday of multi-modal activity data.

    Args:
        db_path: Path to the activity.db file.
        hours: Duration in hours to generate.
        start_time: When to start. Defaults to (now - hours).

    Returns:
        Dict with counts per modality and total.
    """
    if start_time is None:
        start_time = datetime.now() - timedelta(hours=hours)

    start_ts = start_time.timestamp()
    end_ts = start_ts + (hours * 3600)

    logger.info(
        "Generating %s hours of data: %s -> %s",
        hours,
        start_time.strftime("%Y-%m-%d %H:%M"),
        datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M"),
    )

    # Generate all event streams
    all_events: list[ContextEvent] = []

    screen = generate_screen_events(start_ts, end_ts, interval=7.0)
    all_events.extend(screen)
    logger.info("  Screen events: %d", len(screen))

    voice = generate_voice_events(start_ts, end_ts, events_per_hour=10)
    all_events.extend(voice)
    logger.info("  Voice events: %d", len(voice))

    typing = generate_typing_events(start_ts, end_ts, bursts_per_hour=60)
    all_events.extend(typing)
    logger.info("  Typing events: %d", len(typing))

    mouse = generate_mouse_events(start_ts, end_ts, events_per_hour=200)
    all_events.extend(mouse)
    logger.info("  Mouse events: %d", len(mouse))

    clipboard = generate_clipboard_events(start_ts, end_ts, events_per_hour=15)
    all_events.extend(clipboard)
    logger.info("  Clipboard events: %d", len(clipboard))

    correlated = generate_correlated_sequences(start_ts, end_ts, sequences_per_hour=5)
    all_events.extend(correlated)
    logger.info("  Correlated sequences: %d events", len(correlated))

    # Sort by timestamp for realistic insertion order
    all_events.sort(key=lambda e: e.timestamp)
    logger.info("  Total events: %d", len(all_events))

    # Write to DB via EventBus
    bus = EventBus(db_path)
    inserted = 0
    errors = 0
    for event in all_events:
        try:
            bus.emit(event)
            inserted += 1
        except Exception as exc:
            errors += 1
            if errors <= 5:
                logger.warning("Failed to emit event: %s", exc)

    bus.close()

    # Tally by modality
    counts: dict[str, int] = {}
    for e in all_events:
        mod = e.modality.value
        counts[mod] = counts.get(mod, 0) + 1

    counts["_total"] = inserted
    counts["_errors"] = errors
    logger.info("Inserted %d events (%d errors)", inserted, errors)
    return counts


def cleanup_soak_data(db_path: Path | str) -> int:
    """Remove all soak test data from the database.

    Deletes events where the payload contains the soak marker.

    Returns:
        Number of rows deleted.
    """
    import sqlite3

    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")

    cursor = conn.execute(
        "DELETE FROM events WHERE json_extract(payload, '$._soak_test') = 1"
    )
    deleted = cursor.rowcount
    conn.commit()

    # Rebuild FTS index after bulk delete
    try:
        conn.execute("INSERT INTO events_fts(events_fts) VALUES('rebuild')")
        conn.commit()
    except Exception:
        pass  # FTS rebuild is best-effort

    conn.close()
    logger.info("Cleaned up %d soak test events", deleted)
    return deleted


def get_default_db_path() -> Path:
    """Return the default activity.db path from ContextPulse config."""
    try:
        from contextpulse_core.config import ACTIVITY_DB_PATH
        return ACTIVITY_DB_PATH
    except ImportError:
        return Path.home() / "screenshots" / "activity.db"


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generate realistic soak test data for ContextPulse Pro features",
    )
    parser.add_argument(
        "--hours", type=float, default=24.0,
        help="Hours of data to generate (default: 24)",
    )
    parser.add_argument(
        "--start-time", type=str, default=None,
        help="Start timestamp ISO format (default: now - hours). Example: 2026-03-26T08:00:00",
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help="Path to activity.db (default: auto-detect from config)",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="Remove all soak test data instead of generating",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    db_path = Path(args.db) if args.db else get_default_db_path()
    logger.info("Using database: %s", db_path)

    if args.cleanup:
        deleted = cleanup_soak_data(db_path)
        print(f"Cleaned up {deleted} soak test events from {db_path}")
        return

    start_time = None
    if args.start_time:
        start_time = datetime.fromisoformat(args.start_time)

    counts = generate_all(db_path, hours=args.hours, start_time=start_time)

    print("\n=== Soak Data Generation Complete ===")
    print(f"Database: {db_path}")
    print(f"Duration: {args.hours} hours")
    for mod, count in sorted(counts.items()):
        if not mod.startswith("_"):
            print(f"  {mod:>10}: {count:,} events")
    print(f"  {'TOTAL':>10}: {counts['_total']:,} inserted")
    if counts.get("_errors"):
        print(f"  {'ERRORS':>10}: {counts['_errors']:,}")


if __name__ == "__main__":
    main()
