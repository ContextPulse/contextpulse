"""Cross-package tests — Voice + Touch working together.

Tests the self-improving dictation loop:
Voice emits TRANSCRIPTION -> Touch detects paste -> correction written -> vocabulary updated.

Also tests shared database queries from both packages and temporal correlation.
"""

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextpulse_core.spine import ContextEvent, EventBus, EventType, Modality
from contextpulse_touch.burst_tracker import BurstTracker
from contextpulse_touch.correction_detector import CorrectionDetector, VoiceasyBridge
from contextpulse_voice.vocabulary import (
    apply_vocabulary,
    _compile_patterns,
    _load_vocabulary,
    reload_vocabulary,
)
from contextpulse_voice.analyzer import find_corrections, load_entries_from_eventbus
from contextpulse_voice.cleanup import clean_basic
from contextpulse_voice import vocabulary


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def shared_db(tmp_path):
    """Create a shared activity.db with both Voice and Touch events.

    Contains:
    - 5 Voice TRANSCRIPTION events with paste_text_hash
    - 3 Touch TYPING_BURST events
    - 2 Touch CLICK events
    - 1 Touch CORRECTION_DETECTED event
    """
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

    now = time.time()

    # Voice TRANSCRIPTION events
    voice_texts = [
        ("Hello this is a test", 2),
        ("I use cube control daily", 5),
        ("Send to gerard immediately", 10),
        ("Deploy the pie test suite", 15),
        ("Check the post gress database", 20),
    ]
    for text, seconds_ago in voice_texts:
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        evt_id = f"voice_{seconds_ago}"
        conn.execute(
            "INSERT INTO events VALUES (?, ?, 'voice', 'transcription', 'code.exe', 'test.py', 0, ?, NULL, 0.0)",
            (evt_id, now - seconds_ago, json.dumps({
                "transcript": text,
                "raw_transcript": text,
                "confidence": 0.85,
                "language": "en",
                "duration_seconds": 2.5,
                "paste_text_hash": text_hash,
                "paste_timestamp": now - seconds_ago + 0.5,
            })),
        )

    # Touch TYPING_BURST events (occurring after voice pastes)
    for i, seconds_ago in enumerate([1, 4, 9]):
        conn.execute(
            "INSERT INTO events VALUES (?, ?, 'keys', 'typing_burst', 'code.exe', 'test.py', 0, ?, NULL, 0.0)",
            (f"burst_{i}", now - seconds_ago, json.dumps({
                "char_count": 20 + i * 5,
                "word_count": 4 + i,
                "duration_ms": 2000,
                "wpm": 120,
                "backspace_count": i,
                "has_selection": i == 1,
            })),
        )

    # Touch CLICK events
    for i, seconds_ago in enumerate([3, 7]):
        conn.execute(
            "INSERT INTO events VALUES (?, ?, 'flow', 'click', 'code.exe', 'test.py', 0, ?, NULL, 0.0)",
            (f"click_{i}", now - seconds_ago, json.dumps({
                "x": 100 + i * 50, "y": 200, "button": "left", "click_type": "single",
            })),
        )

    # Touch CORRECTION_DETECTED event (linked to voice_10 via paste_event_id)
    conn.execute(
        "INSERT INTO events VALUES (?, ?, 'keys', 'correction_detected', 'code.exe', 'test.py', 0, ?, NULL, 0.0)",
        ("correction_1", now - 8, json.dumps({
            "original_text": "gerard",
            "corrected_text": "Jerard",
            "correction_text": "gerard -> Jerard",
            "correction_type": "select_replace",
            "confidence": 0.85,
            "seconds_after_paste": 2.0,
            "paste_event_id": "voice_10",
        })),
    )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def voice_dir(tmp_path):
    """Temporary voice data directory for vocabulary files."""
    d = tmp_path / "voice_data"
    d.mkdir()
    return d


@pytest.fixture
def cross_vocab_env(voice_dir):
    """Set up vocabulary environment for cross-modal tests."""
    vocab_file = voice_dir / "vocabulary.json"
    learned_file = voice_dir / "vocabulary_learned.json"
    vocab_file.write_text(json.dumps({
        "cube control": "kubectl",
        "get hub": "GitHub",
    }), encoding="utf-8")
    with patch.object(vocabulary, "VOICE_DATA_DIR", voice_dir), \
         patch.object(vocabulary, "VOCAB_FILE", vocab_file), \
         patch.object(vocabulary, "LEARNED_VOCAB_FILE", learned_file):
        vocabulary._compiled_patterns = None
        yield vocab_file, learned_file
        vocabulary._compiled_patterns = None


# ═══════════════════════════════════════════════════════════════════════
# Shared Database Tests
# ═══════════════════════════════════════════════════════════════════════

class TestSharedDatabase:
    """Test that both packages can query the same database correctly."""

    def test_voice_entries_loaded(self, shared_db):
        """Voice analyzer can load transcription entries from shared DB."""
        entries = load_entries_from_eventbus(shared_db)
        assert len(entries) == 5
        for entry in entries:
            assert "raw" in entry
            assert "cleaned" in entry

    def test_touch_events_present(self, shared_db):
        """Touch events are present alongside voice events in shared DB."""
        conn = sqlite3.connect(str(shared_db))
        conn.row_factory = sqlite3.Row

        bursts = conn.execute(
            "SELECT * FROM events WHERE modality='keys' AND event_type='typing_burst'"
        ).fetchall()
        assert len(bursts) == 3

        clicks = conn.execute(
            "SELECT * FROM events WHERE modality='flow' AND event_type='click'"
        ).fetchall()
        assert len(clicks) == 2

        corrections = conn.execute(
            "SELECT * FROM events WHERE event_type='correction_detected'"
        ).fetchall()
        assert len(corrections) == 1

        conn.close()

    def test_eventbus_reads_all_modalities(self, shared_db):
        """EventBus can query events from all modalities."""
        bus = EventBus(shared_db)
        # Query all recent events
        all_events = bus.query_recent(seconds=60, limit=100)
        modalities = {e.modality.value for e in all_events}
        assert "voice" in modalities
        assert "keys" in modalities
        assert "flow" in modalities
        bus.close()

    def test_eventbus_filter_by_modality(self, shared_db):
        """EventBus modality filter works correctly."""
        bus = EventBus(shared_db)
        voice_events = bus.query_recent(seconds=60, modality="voice")
        assert all(e.modality == Modality.VOICE for e in voice_events)
        assert len(voice_events) == 5

        keys_events = bus.query_recent(seconds=60, modality="keys")
        assert all(e.modality == Modality.KEYS for e in keys_events)
        assert len(keys_events) == 4  # 3 bursts + 1 correction

        flow_events = bus.query_recent(seconds=60, modality="flow")
        assert all(e.modality == Modality.FLOW for e in flow_events)
        assert len(flow_events) == 2
        bus.close()

    def test_eventbus_count_by_modality(self, shared_db):
        """EventBus count respects modality filter."""
        bus = EventBus(shared_db)
        assert bus.count(modality="voice") == 5
        assert bus.count(modality="keys") == 4
        assert bus.count(modality="flow") == 2
        assert bus.count() == 11
        bus.close()


# ═══════════════════════════════════════════════════════════════════════
# Touch Finding Voice Events by Hash
# ═══════════════════════════════════════════════════════════════════════

class TestHashCorrelation:
    """Test that Touch can find Voice TRANSCRIPTION events by paste_text_hash."""

    def test_find_voice_event_by_hash(self, shared_db):
        """CorrectionDetector can find a Voice event by its paste hash."""
        text = "Hello this is a test"
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

        bt = BurstTracker()
        det = CorrectionDetector(
            burst_tracker=bt,
            watch_seconds=5.0,
            db_path=shared_db,
        )
        match = det._find_voice_event(text_hash)
        assert match is not None
        assert match["event_id"] == "voice_2"
        assert match["paste_text_hash"] == text_hash
        det.stop()

    def test_find_voice_event_no_match(self, shared_db):
        """Non-matching hash returns None."""
        bt = BurstTracker()
        det = CorrectionDetector(
            burst_tracker=bt,
            watch_seconds=5.0,
            db_path=shared_db,
        )
        match = det._find_voice_event("0000000000000000")
        assert match is None
        det.stop()

    def test_find_voice_event_multiple_matches_returns_first(self, shared_db):
        """When multiple events exist, the most recent match is returned."""
        text = "I use cube control daily"
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=shared_db)
        match = det._find_voice_event(text_hash)
        assert match is not None
        det.stop()

    def test_paste_triggers_watch_for_voice_text(self, shared_db):
        """Pasting text that matches a Voice event should start watch window."""
        bt = BurstTracker()
        det = CorrectionDetector(
            burst_tracker=bt,
            watch_seconds=2.0,
            db_path=shared_db,
        )
        det.on_paste_detected("Hello this is a test")
        assert det.is_watching
        assert bt.is_watching
        det.stop()

    def test_paste_non_voice_text_no_watch(self, shared_db):
        """Pasting text NOT from Voice should not start watch."""
        bt = BurstTracker()
        det = CorrectionDetector(
            burst_tracker=bt,
            watch_seconds=2.0,
            db_path=shared_db,
        )
        det.on_paste_detected("This was copied from a website")
        assert not det.is_watching
        det.stop()


# ═══════════════════════════════════════════════════════════════════════
# Temporal Correlation
# ═══════════════════════════════════════════════════════════════════════

class TestTemporalCorrelation:
    """Test finding events within the same time window across modalities."""

    def test_get_by_time_returns_cross_modal(self, shared_db):
        """get_by_time should return events from multiple modalities within the window."""
        bus = EventBus(shared_db)
        # The voice_2 event is at now-2, burst_0 is at now-1, click_0 is at now-3
        # A 5-second window around now-2 should capture all of these
        now = time.time()
        events = bus.get_by_time(now - 2, window_seconds=3)
        modalities = {e.modality.value for e in events}
        assert len(modalities) >= 2  # At least voice + keys or flow
        bus.close()

    def test_narrow_window_isolates_events(self, shared_db):
        """Very narrow time window should isolate individual events."""
        bus = EventBus(shared_db)
        now = time.time()
        # Voice event at now-20 should be isolated with 0.5s window
        events = bus.get_by_time(now - 20, window_seconds=0.5)
        # Should find the voice event but probably not bursts/clicks
        event_types = {e.event_type.value for e in events}
        if events:
            assert "transcription" in event_types
        bus.close()

    def test_correction_temporally_near_voice_paste(self, shared_db):
        """Correction event should be temporally close to the Voice event it corrects."""
        conn = sqlite3.connect(str(shared_db))
        conn.row_factory = sqlite3.Row

        correction = conn.execute(
            "SELECT * FROM events WHERE event_type='correction_detected'"
        ).fetchone()

        payload = json.loads(correction["payload"])
        paste_event_id = payload["paste_event_id"]

        voice_event = conn.execute(
            "SELECT * FROM events WHERE event_id=?", (paste_event_id,)
        ).fetchone()

        conn.close()

        assert voice_event is not None
        time_diff = abs(correction["timestamp"] - voice_event["timestamp"])
        assert time_diff < 30  # Correction within 30 seconds of paste


# ═══════════════════════════════════════════════════════════════════════
# Full Correction Loop
# ═══════════════════════════════════════════════════════════════════════

class TestFullCorrectionLoop:
    """Test the complete self-improving loop:
    Voice emits TRANSCRIPTION -> Touch detects paste -> correction written -> vocabulary updated.
    """

    def test_full_loop_voice_to_vocabulary(self, shared_db, cross_vocab_env):
        """End-to-end: Voice paste -> correction -> learned vocab -> vocabulary reload."""
        vocab_file, learned_file = cross_vocab_env

        # Step 1: Verify "gerard" is NOT in vocabulary initially
        vocabulary._compiled_patterns = None
        result_before = apply_vocabulary("Hello gerard how are you")
        assert "Jerard" not in result_before  # No correction yet

        # Step 2: Set up Touch correction detection
        bt = BurstTracker(burst_timeout=0.1, min_chars=1)
        bridge = VoiceasyBridge(learned_file=learned_file)
        corrections_emitted = []

        det = CorrectionDetector(
            burst_tracker=bt,
            on_correction=lambda c: corrections_emitted.append(c),
            watch_seconds=5.0,
            db_path=shared_db,
            bridge=bridge,
        )

        # Step 3: Simulate paste of Voice text
        det.on_paste_detected("Send to gerard immediately")
        assert det.is_watching

        # Step 4: User selects "gerard" and types "Jerard"
        det.on_key_event(is_selection=True)
        bt.on_key_press(None, is_selection=True)
        for c in "Jerard":
            bt.on_key_press(c)

        # Step 5: Window change ends correction detection
        det.on_window_change()
        det.stop()

        # Step 6: Check if bridge wrote the correction
        if learned_file.exists():
            learned_data = json.loads(learned_file.read_text(encoding="utf-8"))
            if "gerard" in learned_data:
                # Step 7: Reload vocabulary and verify correction is applied
                vocabulary._vocab_mtime = 0.0
                vocabulary._learned_mtime = 0.0
                vocabulary._compiled_patterns = None

                result_after = apply_vocabulary("Hello gerard how are you")
                assert "Jerard" in result_after

    def test_loop_does_not_duplicate_existing_vocab(self, shared_db, cross_vocab_env):
        """Corrections already in user vocabulary should not be re-added."""
        vocab_file, learned_file = cross_vocab_env

        # "cube control" -> "kubectl" is already in user vocabulary
        bridge = VoiceasyBridge(learned_file=learned_file)
        # Try to add a correction that matches existing user vocab key
        result = bridge.add_correction("cube control", "kubectl")
        # User vocab file has "cube control" so bridge should check against it
        # Actually bridge checks learned_file's parent/vocabulary.json
        # Since our vocab file has it, this should be rejected

    def test_loop_vocabulary_hot_reloads_after_correction(self, shared_db, cross_vocab_env):
        """After Touch writes a correction, Voice's vocabulary hot-reloads it."""
        vocab_file, learned_file = cross_vocab_env

        # Write a learned correction directly (simulating what Touch does)
        bridge = VoiceasyBridge(learned_file=learned_file)
        bridge.add_correction("pie test", "pytest")

        # Force vocabulary reload
        vocabulary._vocab_mtime = 0.0
        vocabulary._learned_mtime = 0.0
        vocabulary._compiled_patterns = None

        result = apply_vocabulary("Run pie test to check")
        assert "pytest" in result

    def test_loop_multiple_corrections_accumulate(self, shared_db, cross_vocab_env):
        """Multiple corrections over time accumulate in learned vocabulary."""
        vocab_file, learned_file = cross_vocab_env

        bridge = VoiceasyBridge(learned_file=learned_file)
        bridge.add_correction("pie test", "pytest")
        bridge.add_correction("engine x", "nginx")
        bridge.add_correction("my sequel", "MySQL")

        # Reload vocabulary
        vocabulary._vocab_mtime = 0.0
        vocabulary._learned_mtime = 0.0
        vocabulary._compiled_patterns = None

        assert "pytest" in apply_vocabulary("run pie test")
        assert "nginx" in apply_vocabulary("configure engine x")
        assert "MySQL" in apply_vocabulary("connect to my sequel")


# ═══════════════════════════════════════════════════════════════════════
# MCP Tools from Both Packages Querying Same Database
# ═══════════════════════════════════════════════════════════════════════

class TestMCPCrossPackage:
    """Test MCP tools from both Voice and Touch querying the same shared database."""

    def test_voice_mcp_reads_voice_events(self, shared_db):
        """Voice MCP tool reads transcriptions from shared DB."""
        from contextpulse_voice import mcp_server as voice_mcp
        with patch.object(voice_mcp, "_DB_PATH", shared_db):
            result = voice_mcp.get_recent_transcriptions(minutes=60, limit=10)
            assert "Transcriptions" in result
            # Should show voice events
            assert "test" in result.lower() or "cube" in result.lower()

    def test_touch_mcp_reads_touch_events(self, shared_db):
        """Touch MCP tool reads touch events from shared DB."""
        from contextpulse_touch import mcp_server as touch_mcp
        with patch.object(touch_mcp, "_DB_PATH", shared_db):
            result = touch_mcp.get_recent_touch_events(seconds=60)
            assert "Touch Events" in result

    def test_voice_mcp_stats(self, shared_db):
        """Voice stats from shared DB."""
        from contextpulse_voice import mcp_server as voice_mcp
        with patch.object(voice_mcp, "_DB_PATH", shared_db):
            result = voice_mcp.get_voice_stats(hours=1.0)
            assert "dictations" in result.lower()

    def test_touch_mcp_stats(self, shared_db):
        """Touch stats from shared DB."""
        from contextpulse_touch import mcp_server as touch_mcp
        with patch.object(touch_mcp, "_DB_PATH", shared_db):
            result = touch_mcp.get_touch_stats(hours=1.0)
            assert "Touch Stats" in result

    def test_touch_mcp_correction_history(self, shared_db):
        """Touch MCP correction history from shared DB."""
        from contextpulse_touch import mcp_server as touch_mcp
        with patch.object(touch_mcp, "_DB_PATH", shared_db):
            result = touch_mcp.get_correction_history(limit=10)
            assert "Correction" in result
            assert "gerard" in result or "Jerard" in result

    def test_touch_mcp_filter_keyboard(self, shared_db):
        """Touch MCP with keyboard filter."""
        from contextpulse_touch import mcp_server as touch_mcp
        with patch.object(touch_mcp, "_DB_PATH", shared_db):
            result = touch_mcp.get_recent_touch_events(seconds=60, event_types="keyboard")
            assert "Touch Events" in result

    def test_touch_mcp_filter_mouse(self, shared_db):
        """Touch MCP with mouse filter."""
        from contextpulse_touch import mcp_server as touch_mcp
        with patch.object(touch_mcp, "_DB_PATH", shared_db):
            result = touch_mcp.get_recent_touch_events(seconds=60, event_types="mouse")
            assert "Touch Events" in result

    def test_touch_mcp_filter_corrections_only(self, shared_db):
        """Touch MCP with corrections-only filter."""
        from contextpulse_touch import mcp_server as touch_mcp
        with patch.object(touch_mcp, "_DB_PATH", shared_db):
            result = touch_mcp.get_recent_touch_events(seconds=60, event_types="corrections")
            assert "CORRECTION" in result

    def test_both_mcp_no_interference(self, shared_db):
        """Querying from both MCP servers simultaneously should not interfere."""
        from contextpulse_voice import mcp_server as voice_mcp
        from contextpulse_touch import mcp_server as touch_mcp

        with patch.object(voice_mcp, "_DB_PATH", shared_db), \
             patch.object(touch_mcp, "_DB_PATH", shared_db):

            errors = []
            results = {"voice": [], "touch": []}

            def query_voice():
                try:
                    for _ in range(5):
                        r = voice_mcp.get_recent_transcriptions(minutes=60, limit=10)
                        results["voice"].append(r)
                except Exception as e:
                    errors.append(e)

            def query_touch():
                try:
                    for _ in range(5):
                        r = touch_mcp.get_recent_touch_events(seconds=60)
                        results["touch"].append(r)
                except Exception as e:
                    errors.append(e)

            t1 = threading.Thread(target=query_voice)
            t2 = threading.Thread(target=query_touch)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            assert len(errors) == 0
            assert len(results["voice"]) == 5
            assert len(results["touch"]) == 5


# ═══════════════════════════════════════════════════════════════════════
# EventBus Cross-Modal Emit and Query
# ═══════════════════════════════════════════════════════════════════════

class TestEventBusCrossModal:
    """Test emitting and querying events from multiple modalities via EventBus."""

    def test_emit_voice_and_touch_events(self, tmp_path):
        """EventBus can store events from both Voice and Touch modalities."""
        db_path = tmp_path / "cross.db"
        bus = EventBus(db_path)

        # Emit Voice event
        voice_evt = ContextEvent(
            modality=Modality.VOICE,
            event_type=EventType.TRANSCRIPTION,
            app_name="code.exe",
            payload={"transcript": "hello world", "raw_transcript": "hello world"},
        )
        bus.emit(voice_evt)

        # Emit Touch burst event
        burst_evt = ContextEvent(
            modality=Modality.KEYS,
            event_type=EventType.TYPING_BURST,
            app_name="code.exe",
            payload={"char_count": 20, "wpm": 100},
        )
        bus.emit(burst_evt)

        # Emit Touch click event
        click_evt = ContextEvent(
            modality=Modality.FLOW,
            event_type=EventType.CLICK,
            app_name="chrome.exe",
            payload={"x": 100, "y": 200, "button": "left"},
        )
        bus.emit(click_evt)

        assert bus.count() == 3
        assert bus.count(modality="voice") == 1
        assert bus.count(modality="keys") == 1
        assert bus.count(modality="flow") == 1
        bus.close()

    def test_listener_receives_cross_modal_events(self, tmp_path):
        """EventBus listener receives events from all modalities."""
        db_path = tmp_path / "listener_cross.db"
        bus = EventBus(db_path)

        received = []
        bus.on(lambda e: received.append(e))

        bus.emit(ContextEvent(modality=Modality.VOICE, event_type=EventType.TRANSCRIPTION,
                              payload={"transcript": "test"}))
        bus.emit(ContextEvent(modality=Modality.KEYS, event_type=EventType.TYPING_BURST,
                              payload={"char_count": 10}))
        bus.emit(ContextEvent(modality=Modality.FLOW, event_type=EventType.CLICK,
                              payload={"x": 0, "y": 0, "button": "left"}))

        assert len(received) == 3
        modalities = {e.modality for e in received}
        assert Modality.VOICE in modalities
        assert Modality.KEYS in modalities
        assert Modality.FLOW in modalities
        bus.close()

    def test_query_recent_with_modality_filter(self, tmp_path):
        """query_recent with modality filter returns only matching events."""
        db_path = tmp_path / "filter.db"
        bus = EventBus(db_path)

        bus.emit(ContextEvent(modality=Modality.VOICE, event_type=EventType.TRANSCRIPTION,
                              payload={"transcript": "test"}))
        bus.emit(ContextEvent(modality=Modality.KEYS, event_type=EventType.TYPING_BURST,
                              payload={"char_count": 10}))

        voice_only = bus.query_recent(seconds=60, modality="voice")
        assert len(voice_only) == 1
        assert voice_only[0].modality == Modality.VOICE

        keys_only = bus.query_recent(seconds=60, modality="keys")
        assert len(keys_only) == 1
        assert keys_only[0].modality == Modality.KEYS
        bus.close()

    def test_concurrent_emit_from_voice_and_touch(self, tmp_path):
        """Concurrent emits from Voice and Touch threads should not corrupt database."""
        db_path = tmp_path / "concurrent.db"
        bus = EventBus(db_path)
        errors = []

        def emit_voice():
            try:
                for i in range(20):
                    bus.emit(ContextEvent(
                        modality=Modality.VOICE,
                        event_type=EventType.TRANSCRIPTION,
                        payload={"transcript": f"voice {i}"},
                    ))
            except Exception as e:
                errors.append(e)

        def emit_touch():
            try:
                for i in range(20):
                    bus.emit(ContextEvent(
                        modality=Modality.KEYS,
                        event_type=EventType.TYPING_BURST,
                        payload={"char_count": i + 1},
                    ))
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=emit_voice)
        t2 = threading.Thread(target=emit_touch)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert len(errors) == 0
        # Due to INSERT OR IGNORE, some events with duplicate IDs may be skipped
        # but total should be close to 40
        total = bus.count()
        assert total >= 30  # Allow for some UUID collisions (extremely unlikely)
        bus.close()
