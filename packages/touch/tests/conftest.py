"""Shared fixtures for touch package tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add source paths
TOUCH_SRC = Path(__file__).parent.parent / "src"
VOICE_SRC = Path(__file__).parent.parent.parent / "voice" / "src"
CORE_SRC = Path(__file__).parent.parent.parent / "core" / "src"
sys.path.insert(0, str(TOUCH_SRC))
sys.path.insert(0, str(VOICE_SRC))
sys.path.insert(0, str(CORE_SRC))

# Save originals before mocking
_MOCKED_MODULES = [
    "pynput", "pynput.keyboard", "pynput.mouse",
    "sounddevice", "faster_whisper", "pyautogui", "pyperclip",
    "anthropic",
]
_original_modules = {k: sys.modules.get(k) for k in _MOCKED_MODULES}

# Mock platform-specific modules
mock_pynput = MagicMock()
sys.modules["pynput"] = mock_pynput
sys.modules["pynput.keyboard"] = mock_pynput.keyboard
sys.modules["pynput.mouse"] = mock_pynput.mouse

mock_sounddevice = MagicMock()
sys.modules["sounddevice"] = mock_sounddevice

mock_faster_whisper = MagicMock()
sys.modules["faster_whisper"] = mock_faster_whisper

mock_pyautogui = MagicMock()
sys.modules["pyautogui"] = mock_pyautogui

mock_pyperclip = MagicMock()
sys.modules["pyperclip"] = mock_pyperclip

mock_anthropic = MagicMock()
sys.modules["anthropic"] = mock_anthropic

import ctypes
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()

# Set up a mock platform provider so tests work on any OS
from contextpulse_core.platform import factory as _platform_factory
from contextpulse_core.platform.base import PlatformProvider

if _platform_factory._instance is None:
    _mock_platform = MagicMock(spec=PlatformProvider)
    _mock_platform.get_foreground_window_title.return_value = ""
    _mock_platform.get_foreground_process_name.return_value = ""
    _mock_platform.get_cursor_pos.return_value = (500, 500)
    _mock_platform.get_clipboard_sequence.return_value = 0
    _mock_platform.get_clipboard_text.return_value = None
    _mock_platform.get_caret_position.return_value = None
    _mock_platform.acquire_single_instance_lock.return_value = object()
    _platform_factory._instance = _mock_platform


@pytest.fixture(scope="session", autouse=True)
def restore_mocked_modules():
    yield
    for mod_name, original in _original_modules.items():
        if original is None:
            sys.modules.pop(mod_name, None)
        else:
            sys.modules[mod_name] = original


@pytest.fixture
def activity_db(tmp_path):
    """Create a temp activity.db with Voice transcription events."""
    import json
    import sqlite3
    import time
    import hashlib

    db_path = tmp_path / "activity.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            timestamp REAL NOT NULL,
            modality TEXT NOT NULL,
            event_type TEXT NOT NULL,
            app_name TEXT DEFAULT '',
            window_title TEXT DEFAULT '',
            monitor_index INTEGER DEFAULT 0,
            payload TEXT NOT NULL,
            correlation_id TEXT,
            attention_score REAL DEFAULT 0.0
        )
    """)

    # Insert a recent Voice transcription
    text = "hello world test"
    text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    conn.execute(
        "INSERT INTO events VALUES (?, ?, 'voice', 'transcription', 'code.exe', 'test.py', 0, ?, NULL, 0.0)",
        (
            "voice_evt_1",
            time.time() - 5,  # 5 seconds ago
            json.dumps({
                "transcript": text,
                "raw_transcript": "hello world test",
                "paste_text_hash": text_hash,
                "paste_timestamp": time.time() - 5,
            }),
        ),
    )
    conn.commit()
    conn.close()
    return db_path, text, text_hash
