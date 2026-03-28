"""Shared fixtures for voice package tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add source paths
VOICE_SRC = Path(__file__).parent.parent / "src"
CORE_SRC = Path(__file__).parent.parent.parent / "core" / "src"
sys.path.insert(0, str(VOICE_SRC))
sys.path.insert(0, str(CORE_SRC))

# Save originals before mocking
_MOCKED_MODULES = [
    "sounddevice", "faster_whisper", "pyautogui", "pyperclip",
    "pynput", "pynput.keyboard", "pystray", "anthropic",
]
_original_modules = {k: sys.modules.get(k) for k in _MOCKED_MODULES}

# Mock heavy/platform-specific modules
mock_sounddevice = MagicMock()
sys.modules["sounddevice"] = mock_sounddevice

mock_faster_whisper = MagicMock()
sys.modules["faster_whisper"] = mock_faster_whisper

mock_pyautogui = MagicMock()
sys.modules["pyautogui"] = mock_pyautogui

mock_pyperclip = MagicMock()
sys.modules["pyperclip"] = mock_pyperclip

mock_pynput = MagicMock()
sys.modules["pynput"] = mock_pynput
sys.modules["pynput.keyboard"] = mock_pynput.keyboard

mock_pystray = MagicMock()
sys.modules["pystray"] = mock_pystray

mock_anthropic = MagicMock()
sys.modules["anthropic"] = mock_anthropic

# Mock ctypes.windll for non-Windows
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
    """Restore mocked sys.modules after voice tests complete."""
    yield
    for mod_name, original in _original_modules.items():
        if original is None:
            sys.modules.pop(mod_name, None)
        else:
            sys.modules[mod_name] = original


@pytest.fixture
def voice_data_dir(tmp_path):
    """Create a temporary voice data directory."""
    d = tmp_path / "voice"
    d.mkdir()
    return d


@pytest.fixture
def vocab_files(voice_data_dir):
    """Create vocabulary files in temp dir."""
    import json
    vocab = {"cube control": "kubectl", "get hub": "GitHub"}
    vocab_file = voice_data_dir / "vocabulary.json"
    vocab_file.write_text(json.dumps(vocab), encoding="utf-8")

    learned_file = voice_data_dir / "vocabulary_learned.json"
    learned = {"gerard": "Jerard"}
    learned_file.write_text(json.dumps(learned), encoding="utf-8")

    return vocab_file, learned_file


@pytest.fixture
def activity_db(tmp_path):
    """Create a temporary activity.db with events table and sample data."""
    import json
    import sqlite3
    import time

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

    # Insert sample transcription events
    now = time.time()
    for i in range(5):
        conn.execute(
            "INSERT INTO events VALUES (?, ?, 'voice', 'transcription', 'code.exe', 'test.py', 0, ?, NULL, 0.0)",
            (
                f"evt_{i}",
                now - (i * 60),
                json.dumps({
                    "transcript": f"cleaned text {i}",
                    "raw_transcript": f"raw text {i}",
                    "confidence": 0.85,
                    "language": "en",
                    "duration_seconds": 3.0,
                    "cleanup_applied": i % 2 == 0,
                    "paste_text_hash": f"hash_{i}",
                }),
            ),
        )
    conn.commit()
    conn.close()
    return db_path
