"""Tests for buffer.py — rolling buffer, change detection, pruning."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image


def _make_image(width=100, height=100, color=(128, 128, 128)):
    """Create a solid-color test image."""
    return Image.new("RGB", (width, height), color)


def _make_different_image(width=100, height=100):
    """Create a noisy test image (will differ from solid colors)."""
    arr = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
    return Image.fromarray(arr)


class TestRollingBuffer:
    """Test buffer add, prune, and retrieval."""

    def test_add_stores_frame(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            img = _make_image()
            stored = buf.add(img)
            assert stored is True
            assert buf.frame_count() > 0

    def test_add_identical_frame_skipped(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            img = _make_image(color=(100, 100, 100))
            buf.add(img)
            count_after_first = buf.frame_count()
            # Same image again — should be skipped
            stored = buf.add(img)
            assert stored is False
            assert buf.frame_count() == count_after_first

    def test_add_different_frame_stored(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            img1 = _make_image(color=(0, 0, 0))
            img2 = _make_image(color=(255, 255, 255))
            buf.add(img1)
            count_after_first = buf.frame_count()
            time.sleep(0.01)  # ensure different millisecond timestamp
            stored = buf.add(img2)
            assert stored is True
            assert buf.frame_count() > count_after_first

    def test_list_frames_sorted(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            # Create frames with known timestamps
            for i in range(3):
                img = _make_different_image()
                buf.add(img)
                time.sleep(0.01)  # ensure different timestamps
            frames = buf.list_frames()
            assert len(frames) == 3
            # Should be sorted oldest-first
            stems = [int(f.stem) for f in frames]
            assert stems == sorted(stems)

    def test_get_latest_returns_newest(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            buf.add(_make_different_image())
            time.sleep(0.01)
            buf.add(_make_different_image())
            latest = buf.get_latest()
            frames = buf.list_frames()
            assert latest == frames[-1]

    def test_get_latest_empty_returns_none(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            assert buf.get_latest() is None

    def test_clear_removes_all(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            buf.add(_make_different_image())
            buf.add(_make_different_image())
            assert buf.frame_count() > 0
            buf.clear()
            assert buf.frame_count() == 0


class TestChangeDetection:
    """Test the pixel-difference change detection logic."""

    def test_identical_arrays_no_change(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            arr = np.full((100, 100, 3), 128, dtype=np.uint8)
            buf._last_frame = arr
            assert not buf._has_changed(arr)

    def test_very_different_arrays_changed(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            arr1 = np.zeros((100, 100, 3), dtype=np.uint8)
            arr2 = np.full((100, 100, 3), 255, dtype=np.uint8)
            buf._last_frame = arr1
            assert buf._has_changed(arr2)

    def test_different_shape_always_changed(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            arr1 = np.zeros((100, 100, 3), dtype=np.uint8)
            arr2 = np.zeros((200, 200, 3), dtype=np.uint8)
            buf._last_frame = arr1
            assert buf._has_changed(arr2) is True

    def test_no_previous_frame_always_changed(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            arr = np.zeros((100, 100, 3), dtype=np.uint8)
            assert buf._has_changed(arr) is True


class TestPruning:
    """Test that old frames are pruned correctly."""

    def test_old_frames_pruned(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir), \
             patch("contextpulse_sight.buffer.BUFFER_MAX_AGE", 1):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            # Create a frame with an old timestamp (10 seconds ago)
            old_ts = int((time.time() - 10) * 1000)
            old_frame = tmp_buffer_dir / f"{old_ts}.jpg"
            _make_image().save(old_frame, format="JPEG")
            old_txt = tmp_buffer_dir / f"{old_ts}.txt"
            old_txt.write_text('{"text": "old", "confidence": 0.9}')

            buf._prune()
            assert not old_frame.exists()
            assert not old_txt.exists()

    def test_recent_frames_kept(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir), \
             patch("contextpulse_sight.buffer.BUFFER_MAX_AGE", 300):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            # Create a recent frame
            recent_ts = int(time.time() * 1000)
            recent_frame = tmp_buffer_dir / f"{recent_ts}.jpg"
            _make_image().save(recent_frame, format="JPEG")

            buf._prune()
            assert recent_frame.exists()

    def test_invalid_filename_skipped(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            # Create a file with non-numeric name
            junk = tmp_buffer_dir / "not_a_timestamp.jpg"
            junk.write_text("junk")
            # Should not crash
            buf._prune()
            # Junk file should still exist (not parseable, skipped)
            assert junk.exists()


class TestOCRText:
    """Test OCR text storage alongside frames."""

    def test_add_ocr_text(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            frame_path = tmp_buffer_dir / "1234567890.jpg"
            _make_image().save(frame_path, format="JPEG")

            buf.add_ocr_text(frame_path, "hello world", 0.95)
            txt_path = frame_path.with_suffix(".txt")
            assert txt_path.exists()
            data = json.loads(txt_path.read_text())
            assert data["text"] == "hello world"
            assert data["confidence"] == 0.95

    def test_get_latest_context_prefers_text(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            ts = int(time.time() * 1000)
            frame_path = tmp_buffer_dir / f"{ts}.jpg"
            _make_image().save(frame_path, format="JPEG")
            buf.add_ocr_text(frame_path, "screen text", 0.90)

            ctx = buf.get_latest_context()
            assert ctx["type"] == "text"
            assert ctx["content"] == "screen text"

    def test_get_latest_context_falls_back_to_image(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            ts = int(time.time() * 1000)
            frame_path = tmp_buffer_dir / f"{ts}.jpg"
            _make_image().save(frame_path, format="JPEG")

            ctx = buf.get_latest_context()
            assert ctx["type"] == "image"
            assert ctx["path"] == frame_path
