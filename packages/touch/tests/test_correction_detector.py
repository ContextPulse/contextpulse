"""Tests for CorrectionDetector and VoiceasyBridge."""

import hashlib
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextpulse_touch.burst_tracker import BurstTracker
from contextpulse_touch.correction_detector import CorrectionDetector, VoiceasyBridge


class TestVoiceasyBridge:
    def test_add_correction(self, tmp_path):
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        result = bridge.add_correction("cube control", "kubectl")
        assert result is True
        assert learned_file.exists()
        data = json.loads(learned_file.read_text(encoding="utf-8"))
        assert data["cube control"] == "kubectl"

    def test_duplicate_rejected(self, tmp_path):
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        bridge.add_correction("gerard", "Jerard")
        result = bridge.add_correction("gerard", "Jerard2")
        assert result is False  # Already exists (same key)

    def test_same_word_rejected(self, tmp_path):
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        result = bridge.add_correction("hello", "hello")
        assert result is False

    def test_empty_rejected(self, tmp_path):
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        assert bridge.add_correction("", "test") is False
        assert bridge.add_correction("test", "") is False

    def test_user_vocab_conflict(self, tmp_path):
        voice_dir = tmp_path / "voice"
        voice_dir.mkdir()
        learned_file = voice_dir / "vocabulary_learned.json"
        user_vocab = voice_dir / "vocabulary.json"
        user_vocab.write_text(json.dumps({"existing": "value"}), encoding="utf-8")

        bridge = VoiceasyBridge(learned_file=learned_file)
        result = bridge.add_correction("existing", "new_value")
        assert result is False  # User vocab takes priority

    def test_get_recent_corrections(self, tmp_path):
        learned_file = tmp_path / "voice" / "vocabulary_learned.json"
        bridge = VoiceasyBridge(learned_file=learned_file)
        bridge.add_correction("gerard", "Jerard")
        bridge.add_correction("cube control", "kubectl")
        corrections = bridge.get_recent_corrections()
        assert len(corrections) == 2

    def test_get_corrections_empty(self, tmp_path):
        bridge = VoiceasyBridge(learned_file=tmp_path / "nonexistent.json")
        assert bridge.get_recent_corrections() == []


class TestCorrectionDetector:
    @pytest.fixture
    def detector(self, activity_db):
        db_path, text, text_hash = activity_db
        bt = BurstTracker(burst_timeout=0.1, min_chars=1)
        on_correction = MagicMock()
        bridge = VoiceasyBridge(learned_file=Path("/tmp/test_learned.json"))
        bridge.add_correction = MagicMock(return_value=True)

        det = CorrectionDetector(
            burst_tracker=bt,
            on_correction=on_correction,
            watch_seconds=1.0,
            db_path=db_path,
            bridge=bridge,
        )
        return det, bt, on_correction, text, text_hash, bridge

    def test_voice_paste_starts_watch(self, detector):
        det, bt, on_correction, text, text_hash, bridge = detector
        det.on_paste_detected(text)
        assert det.is_watching
        assert bt.is_watching
        det.stop()

    def test_non_voice_paste_ignored(self, detector):
        det, bt, on_correction, text, text_hash, bridge = detector
        det.on_paste_detected("random text not from voice")
        assert not det.is_watching
        det.stop()

    def test_empty_paste_ignored(self, detector):
        det, bt, on_correction, text, text_hash, bridge = detector
        det.on_paste_detected("")
        assert not det.is_watching

    def test_paste_counter(self, detector):
        det, bt, on_correction, text, text_hash, bridge = detector
        det.on_paste_detected("something")
        assert det.pastes_detected == 1

    def test_window_change_ends_watch(self, detector):
        det, bt, on_correction, text, text_hash, bridge = detector
        det.on_paste_detected(text)
        assert det.is_watching
        det.on_window_change()
        assert not det.is_watching
        det.stop()

    def test_watch_expires(self, detector):
        det, bt, on_correction, text, text_hash, bridge = detector
        det._watch_seconds = 0.2  # Short window for testing
        det.on_paste_detected(text)
        assert det.is_watching
        time.sleep(0.5)
        assert not det.is_watching
        det.stop()

    def test_stop_cleans_up(self, detector):
        det, bt, on_correction, text, text_hash, bridge = detector
        det.on_paste_detected(text)
        det.stop()
        assert not det.is_watching


class TestCorrectionExtraction:
    def test_extract_backspace_retype(self):
        bt = BurstTracker()
        det = CorrectionDetector(
            burst_tracker=bt,
            db_path=Path("/nonexistent"),
        )
        corrections = det._extract_corrections(
            original="hello worldx",
            typed="world",
            backspace_count=6,  # deleted "worldx"
            has_selection=False,
        )
        assert len(corrections) == 1
        assert corrections[0]["correction_type"] == "backspace_retype"

    def test_extract_select_replace(self):
        bt = BurstTracker()
        det = CorrectionDetector(
            burst_tracker=bt,
            db_path=Path("/nonexistent"),
        )
        corrections = det._extract_corrections(
            original="I use cube control daily",
            typed="kubectl",
            backspace_count=0,
            has_selection=True,
        )
        # Should find a match for one of the original words
        assert len(corrections) >= 1
        assert corrections[0]["correction_type"] == "select_replace"

    def test_no_typed_text(self):
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        assert det._extract_corrections("hello", "", 0, False) == []

    def test_no_original_text(self):
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        assert det._extract_corrections("", "world", 0, False) == []

    def test_char_overlap(self):
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        assert det._char_overlap("hello", "helo") > 0.5
        assert det._char_overlap("abc", "xyz") == 0.0
        assert det._char_overlap("", "test") == 0.0


class TestCorrectionDetectorStats:
    def test_initial_stats(self):
        bt = BurstTracker()
        det = CorrectionDetector(burst_tracker=bt, db_path=Path("/nonexistent"))
        assert det.corrections_detected == 0
        assert det.pastes_detected == 0
