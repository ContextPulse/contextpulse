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
            assert stored  # returns Path (truthy) when stored
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
            assert stored  # returns Path (truthy) when stored
            assert buf.frame_count() > count_after_first

    def test_add_with_monitor_index(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer, parse_frame_path
            buf = RollingBuffer()
            img = _make_image()
            result = buf.add(img, monitor_index=1)
            assert result  # truthy tuple
            path, diff_pct = result
            assert isinstance(path, Path)
            assert isinstance(diff_pct, float)
            assert diff_pct == 100.0  # first frame always 100%
            parsed = parse_frame_path(path)
            assert parsed is not None
            assert parsed[1] == 1  # monitor index

    def test_per_monitor_change_detection(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            img = _make_image(color=(100, 100, 100))
            # Same image on monitor 0 — should store first, skip second
            buf.add(img, monitor_index=0)
            assert buf.add(img, monitor_index=0) is False
            # Same image on monitor 1 — should store (different monitor)
            time.sleep(0.01)
            assert buf.add(img, monitor_index=1)

    def test_list_frames_sorted(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer, parse_frame_path
            buf = RollingBuffer()
            # Create frames with known timestamps
            for i in range(3):
                img = _make_different_image()
                buf.add(img)
                time.sleep(0.01)  # ensure different timestamps
            frames = buf.list_frames()
            assert len(frames) == 3
            # Should be sorted oldest-first by timestamp
            timestamps = [parse_frame_path(f)[0] for f in frames]
            assert timestamps == sorted(timestamps)

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


class TestParseFramePath:
    """Test frame path parsing."""

    def test_parse_new_format(self, tmp_buffer_dir):
        from contextpulse_sight.buffer import parse_frame_path
        path = tmp_buffer_dir / "1711036800123_m0.jpg"
        result = parse_frame_path(path)
        assert result == (1711036800123, 0)

    def test_parse_new_format_monitor_1(self, tmp_buffer_dir):
        from contextpulse_sight.buffer import parse_frame_path
        path = tmp_buffer_dir / "1711036800123_m1.jpg"
        result = parse_frame_path(path)
        assert result == (1711036800123, 1)

    def test_parse_legacy_format(self, tmp_buffer_dir):
        from contextpulse_sight.buffer import parse_frame_path
        path = tmp_buffer_dir / "1711036800123.jpg"
        result = parse_frame_path(path)
        assert result == (1711036800123, 0)

    def test_parse_invalid_returns_none(self, tmp_buffer_dir):
        from contextpulse_sight.buffer import parse_frame_path
        path = tmp_buffer_dir / "not_a_timestamp.jpg"
        result = parse_frame_path(path)
        assert result is None


class TestChangeDetection:
    """Test the pixel-difference change detection logic."""

    def test_identical_arrays_no_change(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            arr = np.full((100, 100, 3), 128, dtype=np.uint8)
            assert not buf._has_changed(arr, arr)

    def test_very_different_arrays_changed(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            arr1 = np.zeros((100, 100, 3), dtype=np.uint8)
            arr2 = np.full((100, 100, 3), 255, dtype=np.uint8)
            assert buf._has_changed(arr2, arr1)

    def test_different_shape_always_changed(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            arr1 = np.zeros((100, 100, 3), dtype=np.uint8)
            arr2 = np.zeros((200, 200, 3), dtype=np.uint8)
            assert buf._has_changed(arr2, arr1) is True

    def test_no_previous_frame_detected_via_add(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            arr = np.zeros((100, 100, 3), dtype=np.uint8)
            img = Image.fromarray(arr)
            # First add always stores (no previous frame for this monitor)
            assert buf.add(img)


class TestPruning:
    """Test that old frames are pruned correctly."""

    def test_old_frames_pruned(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir), \
             patch("contextpulse_sight.buffer.BUFFER_MAX_AGE", 1):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            # Create a frame with an old timestamp (10 seconds ago)
            old_ts = int((time.time() - 10) * 1000)
            old_frame = tmp_buffer_dir / f"{old_ts}_m0.jpg"
            _make_image().save(old_frame, format="JPEG")
            old_txt = tmp_buffer_dir / f"{old_ts}_m0.txt"
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
            recent_frame = tmp_buffer_dir / f"{recent_ts}_m0.jpg"
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
            frame_path = tmp_buffer_dir / "1234567890_m0.jpg"
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
            frame_path = tmp_buffer_dir / f"{ts}_m0.jpg"
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
            frame_path = tmp_buffer_dir / f"{ts}_m0.jpg"
            _make_image().save(frame_path, format="JPEG")

            ctx = buf.get_latest_context()
            assert ctx["type"] == "image"
            assert ctx["path"] == frame_path


class TestTokenEstimation:
    """Test token cost estimation functions."""

    def test_estimate_image_tokens_standard(self):
        from contextpulse_sight.buffer import estimate_image_tokens
        # 1280x720 → ceil(1280/768)=2, ceil(720/768)=1 → 2*1*258 = 516
        assert estimate_image_tokens(1280, 720) == 516

    def test_estimate_image_tokens_4k(self):
        from contextpulse_sight.buffer import estimate_image_tokens
        # 3840x2160 → ceil(3840/768)=5, ceil(2160/768)=3 → 5*3*258 = 3870
        assert estimate_image_tokens(3840, 2160) == 3870

    def test_estimate_image_tokens_small(self):
        from contextpulse_sight.buffer import estimate_image_tokens
        # 100x100 → ceil(100/768)=1, ceil(100/768)=1 → 1*1*258 = 258
        assert estimate_image_tokens(100, 100) == 258

    def test_estimate_text_tokens(self):
        from contextpulse_sight.buffer import estimate_text_tokens
        assert estimate_text_tokens("hello world") == 2  # 11 chars // 4 = 2
        assert estimate_text_tokens("") == 1  # minimum 1

    def test_estimate_text_tokens_long(self):
        from contextpulse_sight.buffer import estimate_text_tokens
        text = "x" * 400
        assert estimate_text_tokens(text) == 100


class TestDiffScore:
    """Test diff percentage computation."""

    def test_diff_pct_identical(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            arr = np.full((100, 100, 3), 128, dtype=np.uint8)
            assert buf._diff_pct(arr, arr) == 0.0

    def test_diff_pct_max(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            arr1 = np.zeros((100, 100, 3), dtype=np.uint8)
            arr2 = np.full((100, 100, 3), 255, dtype=np.uint8)
            assert buf._diff_pct(arr2, arr1) == 100.0

    def test_diff_pct_different_shape(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            arr1 = np.zeros((100, 100, 3), dtype=np.uint8)
            arr2 = np.zeros((200, 200, 3), dtype=np.uint8)
            assert buf._diff_pct(arr2, arr1) == 100.0

    def test_add_returns_diff_pct(self, tmp_buffer_dir):
        with patch("contextpulse_sight.buffer.BUFFER_DIR", tmp_buffer_dir):
            from contextpulse_sight.buffer import RollingBuffer
            buf = RollingBuffer()
            img1 = _make_image(color=(0, 0, 0))
            img2 = _make_image(color=(255, 255, 255))
            result1 = buf.add(img1)
            assert result1  # truthy
            _, diff1 = result1
            assert diff1 == 100.0  # first frame

            time.sleep(0.01)
            result2 = buf.add(img2)
            assert result2
            _, diff2 = result2
            assert diff2 == 100.0  # black to white = 100%
