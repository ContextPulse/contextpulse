"""Tests for ocr_worker.py — background OCR processing."""

import time
from pathlib import Path
from unittest.mock import patch, MagicMock

from PIL import Image

from contextpulse_sight.activity import ActivityDB
from contextpulse_sight.buffer import RollingBuffer
from contextpulse_sight.ocr_worker import OCRWorker


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
