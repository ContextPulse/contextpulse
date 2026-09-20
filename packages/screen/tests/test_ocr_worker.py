"""Tests for ocr_worker.py — background OCR processing."""

import time
from unittest.mock import MagicMock, patch

from contextpulse_core.config import save_config
from contextpulse_sight.activity import ActivityDB
from contextpulse_sight.buffer import RollingBuffer
from contextpulse_sight.ocr_worker import OCRWorker
from PIL import Image


def _make_image(width=100, height=100, color=(128, 128, 128)):
    return Image.new("RGB", (width, height), color)


class TestOCRWorker:
    """Test background OCR processing."""

    def test_enqueue_and_process(self, tmp_path):
        buf_dir = tmp_path / "buffer"
        buf_dir.mkdir()

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            db = ActivityDB(db_path=tmp_path / "test.db")
            buf = RollingBuffer()

            # Create a test frame
            frame_path = buf_dir / "1234567890_m0.jpg"
            _make_image().save(frame_path, format="JPEG")
            row_id = db.record(time.time(), "Test", "test.exe", frame_path=str(frame_path))

            # Mock OCR to return text
            mock_result = {
                "type": "text",
                "text": "Hello OCR World",
                "chars": 15,
                "confidence": 0.92,
                "lines": 1,
                "ocr_time": 0.5,
            }
            with patch("contextpulse_sight.ocr_worker.classify_and_extract", return_value=mock_result):
                worker = OCRWorker(db, buf)
                worker._process(frame_path, row_id)

            # Check OCR text was stored
            txt_path = frame_path.with_suffix(".txt")
            assert txt_path.exists()

            # Check DB was updated
            results = db.search("Hello", minutes_ago=5)
            assert len(results) >= 1
            assert "Hello OCR World" in results[0]["ocr_text"]

            db.close()

    def test_queue_full_drops_frame(self, tmp_path):
        buf_dir = tmp_path / "buffer"
        buf_dir.mkdir()

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            db = ActivityDB(db_path=tmp_path / "test.db")
            buf = RollingBuffer()
            worker = OCRWorker(db, buf)

            # Fill the queue
            for i in range(15):  # Queue maxsize=10
                frame_path = buf_dir / f"{i}_m0.jpg"
                worker.enqueue(frame_path, i)

            # Queue should have at most 10 items
            assert worker._queue.qsize() <= 10
            db.close()

    def test_nonexistent_frame_skipped(self, tmp_path):
        buf_dir = tmp_path / "buffer"
        buf_dir.mkdir()

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            db = ActivityDB(db_path=tmp_path / "test.db")
            buf = RollingBuffer()
            worker = OCRWorker(db, buf)

            # Process a nonexistent frame — should not crash
            worker._process(buf_dir / "nonexistent.jpg", 999)
            db.close()

    def test_process_forwards_monitor_index_to_emit_ocr(self, tmp_path):
        """Regression: cp-ocr-crossmonitor-mislabeled-attribution.

        _process must forward the monitor a frame came from into
        emit_ocr(), so the ContextEvent it produces isn't silently
        stamped monitor_index=0 for every monitor.
        """
        buf_dir = tmp_path / "buffer"
        buf_dir.mkdir()

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            db = ActivityDB(db_path=tmp_path / "test.db")
            buf = RollingBuffer()

            frame_path = buf_dir / "1234567890_m1.jpg"
            _make_image().save(frame_path, format="JPEG")
            row_id = db.record(time.time(), "Test", "test.exe", frame_path=str(frame_path))

            mock_result = {
                "type": "text", "text": "Hello OCR World", "chars": 15,
                "confidence": 0.92, "lines": 1, "ocr_time": 0.5,
            }
            worker = OCRWorker(db, buf)
            mock_module = MagicMock()
            worker.set_sight_module(mock_module)

            with patch("contextpulse_sight.ocr_worker.classify_and_extract", return_value=mock_result):
                worker._process(frame_path, row_id, "test.exe", "Test", monitor_index=1)

            mock_module.emit_ocr.assert_called_once()
            assert mock_module.emit_ocr.call_args.kwargs["monitor_index"] == 1
            db.close()

    def test_enqueue_defaults_monitor_index_to_zero(self, tmp_path):
        """Backward compatible: existing callers that don't pass monitor_index still work."""
        buf_dir = tmp_path / "buffer"
        buf_dir.mkdir()
        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            db = ActivityDB(db_path=tmp_path / "test.db")
            buf = RollingBuffer()
            worker = OCRWorker(db, buf)
            worker.enqueue(buf_dir / "x_m0.jpg", 1)
            item = worker._queue.get_nowait()
            assert item[-1] == 0, f"expected monitor_index to default to 0, queue item was {item}"
            db.close()

    def test_image_type_not_stored(self, tmp_path):
        buf_dir = tmp_path / "buffer"
        buf_dir.mkdir()

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            db = ActivityDB(db_path=tmp_path / "test.db")
            buf = RollingBuffer()

            frame_path = buf_dir / "1234567890_m0.jpg"
            _make_image().save(frame_path, format="JPEG")
            row_id = db.record(time.time(), "Test", "test.exe", frame_path=str(frame_path))

            # Mock OCR to return image type (not enough text)
            mock_result = {
                "type": "image",
                "text": None,
                "chars": 5,
                "confidence": 0.3,
                "lines": 1,
                "ocr_time": 0.5,
            }
            with patch("contextpulse_sight.ocr_worker.classify_and_extract", return_value=mock_result):
                worker = OCRWorker(db, buf)
                worker._process(frame_path, row_id)

            # No .txt sidecar should be created
            txt_path = frame_path.with_suffix(".txt")
            assert not txt_path.exists()
            db.close()


class TestStorageModeAndAlwaysBoth:
    """T9-T10: the two Settings controls this module owns are live.

    Both were module constants read once at import from
    contextpulse_sight.config, which read only the env var -- so neither the
    storage-mode dropdown nor an always-both app list saved in config.json
    ever reached the worker.
    """

    def _text_heavy(self):
        return {
            "type": "text",
            "text": "a long page of text",
            "chars": 19,
            "confidence": 0.95,
            "lines": 3,
            "ocr_time": 0.4,
        }

    def test_storage_mode_visual_stops_enqueue_and_smart_resumes_it(
        self, tmp_path, isolated_config
    ):
        """T9: switching the mode changes behaviour inside one worker."""
        buf_dir = tmp_path / "buffer"
        buf_dir.mkdir()

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            db = ActivityDB(db_path=tmp_path / "test.db")
            worker = OCRWorker(db, RollingBuffer())
            frame_path = buf_dir / "1234567890_m0.jpg"
            _make_image().save(frame_path, format="JPEG")

            save_config({"storage_mode": "visual"})
            worker.enqueue(frame_path, 1)
            assert worker._queue.qsize() == 0, "visual mode must not queue OCR work"

            time.sleep(0.01)
            save_config({"storage_mode": "smart"})
            worker.enqueue(frame_path, 1)
            assert worker._queue.qsize() == 1, (
                "the mode change did not take effect on a running worker"
            )
            db.close()

    def test_always_both_apps_keeps_the_image_case_insensitively(
        self, tmp_path, isolated_config
    ):
        """T10: a saved app name is matched against the lowercased process."""
        buf_dir = tmp_path / "buffer"
        buf_dir.mkdir()
        save_config({"always_both_apps": ["MyApp.EXE"]})

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            db = ActivityDB(db_path=tmp_path / "test.db")
            worker = OCRWorker(db, RollingBuffer())
            frame_path = buf_dir / "1234567891_m0.jpg"
            _make_image().save(frame_path, format="JPEG")
            row_id = db.record(time.time(), "Chart", "myapp.exe", frame_path=str(frame_path))

            with patch(
                "contextpulse_sight.ocr_worker.classify_and_extract",
                return_value=self._text_heavy(),
            ):
                worker._process(frame_path, row_id, app_name="myapp.exe")

            assert frame_path.exists(), (
                "a text-heavy frame from an always-both app had its image "
                "deleted -- the saved list never reached _process"
            )
            db.close()

    def test_an_app_outside_the_list_still_loses_its_image(self, tmp_path, isolated_config):
        """Positive control: without the override, smart mode drops the image."""
        buf_dir = tmp_path / "buffer"
        buf_dir.mkdir()
        save_config({"always_both_apps": ["MyApp.EXE"]})

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            db = ActivityDB(db_path=tmp_path / "test.db")
            worker = OCRWorker(db, RollingBuffer())
            frame_path = buf_dir / "1234567892_m0.jpg"
            _make_image().save(frame_path, format="JPEG")
            row_id = db.record(time.time(), "Notes", "other.exe", frame_path=str(frame_path))

            with patch(
                "contextpulse_sight.ocr_worker.classify_and_extract",
                return_value=self._text_heavy(),
            ):
                worker._process(frame_path, row_id, app_name="other.exe")

            assert not frame_path.exists()
            db.close()
