"""End-to-end integration tests: daemon capture → buffer → MCP server reads.

Verifies the full data flow:
1. App captures frames → saved to buffer on disk
2. MCP server reads from the same buffer directory
3. MCP tools return correct data
"""

import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from contextpulse_sight.buffer import RollingBuffer


def _make_image(width: int = 1280, height: int = 720, color: tuple = (100, 150, 200)) -> Image.Image:
    """Create a test image with a solid color."""
    return Image.new("RGB", (width, height), color)


def _make_different_image(width: int = 1280, height: int = 720) -> Image.Image:
    """Create a test image with random pixels (guaranteed different from solid)."""
    arr = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
    return Image.fromarray(arr)


class TestDaemonToMCPFlow:
    """Test that frames captured by the daemon are readable by MCP tools."""

    def test_capture_lands_in_buffer(self, tmp_path):
        """App.do_quick_capture() stores frames in the shared buffer."""
        buf_dir = tmp_path / "buffer"
        output_dir = tmp_path / "screenshots"
        output_dir.mkdir()

        with (
            patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir),
            patch("contextpulse_sight.config.BUFFER_DIR", buf_dir),
            patch("contextpulse_sight.config.OUTPUT_DIR", output_dir),
            patch("contextpulse_sight.app.FILE_LATEST", output_dir / "screen_latest.png"),
            patch("contextpulse_sight.capture.capture_active_monitor", return_value=_make_image()),
        ):
            from contextpulse_sight.app import ContextPulseSightApp

            app = ContextPulseSightApp()
            # Override app's buffer to use our temp dir
            app.buffer = RollingBuffer()

            app.do_quick_capture()

            # Verify frame landed in buffer
            assert app.buffer.frame_count() >= 1
            frames = app.buffer.list_frames()
            assert len(frames) >= 1
            assert frames[0].suffix == ".jpg"

            # Verify the latest file was also written
            latest = output_dir / "screen_latest.png"
            assert latest.exists()

    def test_mcp_reads_daemon_buffer(self, tmp_path):
        """MCP server can read frames that the daemon wrote to disk."""
        buf_dir = tmp_path / "buffer"
        output_dir = tmp_path / "screenshots"
        output_dir.mkdir()

        with (
            patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir),
            patch("contextpulse_sight.config.BUFFER_DIR", buf_dir),
            patch("contextpulse_sight.config.OUTPUT_DIR", output_dir),
            patch("contextpulse_sight.config.FILE_LATEST", output_dir / "screen_latest.png"),
            patch("contextpulse_sight.capture.capture_active_monitor", return_value=_make_image()),
        ):
            # Simulate daemon writing frames
            daemon_buffer = RollingBuffer()
            img1 = _make_image(color=(100, 100, 100))
            daemon_buffer.add(img1)

            img2 = _make_different_image()
            daemon_buffer.add(img2)

            # MCP server creates its own RollingBuffer reading from the same dir
            mcp_buffer = RollingBuffer()

            assert mcp_buffer.frame_count() >= 2
            recent = mcp_buffer.get_recent(seconds=60)
            assert len(recent) >= 2

            # Verify frames are readable as images
            for frame_path in recent:
                img = Image.open(frame_path)
                assert img.width > 0
                assert img.height > 0

    def test_buffer_status_reflects_daemon_captures(self, tmp_path):
        """get_buffer_status() correctly reports daemon-written frames."""
        buf_dir = tmp_path / "buffer"

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            buf = RollingBuffer()

            # Empty buffer
            assert buf.frame_count() == 0
            assert buf.get_latest() is None

            # Add frames
            buf.add(_make_image(color=(50, 50, 50)))
            buf.add(_make_different_image())

            assert buf.frame_count() >= 2
            latest = buf.get_latest()
            assert latest is not None

            # Verify frame timestamps are recent
            ts = int(latest.stem) / 1000.0
            assert time.time() - ts < 5  # within last 5 seconds

    def test_ocr_text_shared_between_daemon_and_mcp(self, tmp_path):
        """OCR text written by daemon is readable by MCP buffer."""
        buf_dir = tmp_path / "buffer"

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            # Daemon writes frame + OCR text
            daemon_buf = RollingBuffer()
            daemon_buf.add(_make_image())
            frame = daemon_buf.get_latest()
            daemon_buf.add_ocr_text(frame, "Hello World\nLine 2", confidence=0.95)

            # MCP reads context
            mcp_buf = RollingBuffer()
            ctx = mcp_buf.get_latest_context()
            assert ctx["type"] == "text"
            assert "Hello World" in ctx["content"]

    def test_change_detection_prevents_duplicates(self, tmp_path):
        """Identical frames are not stored twice."""
        buf_dir = tmp_path / "buffer"

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            buf = RollingBuffer()
            img = _make_image(color=(128, 128, 128))

            assert buf.add(img) is True  # First frame always stored
            assert buf.add(img) is not True  # Identical frame skipped
            assert buf.frame_count() == 1

    def test_multiple_capture_modes_coexist(self, tmp_path):
        """Quick, all-monitor, and region captures all work without conflict."""
        output_dir = tmp_path / "screenshots"
        output_dir.mkdir()
        buf_dir = tmp_path / "buffer"

        with (
            patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir),
            patch("contextpulse_sight.config.BUFFER_DIR", buf_dir),
            patch("contextpulse_sight.config.OUTPUT_DIR", output_dir),
            patch("contextpulse_sight.app.FILE_LATEST", output_dir / "screen_latest.png"),
            patch("contextpulse_sight.app.FILE_ALL", output_dir / "screen_all.png"),
            patch("contextpulse_sight.app.FILE_REGION", output_dir / "screen_region.png"),
            patch("contextpulse_sight.capture.capture_active_monitor", return_value=_make_image(color=(255, 0, 0))),
            patch("contextpulse_sight.capture.capture_all_monitors", return_value=_make_image(2560, 720, (0, 255, 0))),
            patch("contextpulse_sight.capture.capture_region", return_value=_make_image(800, 600, (0, 0, 255))),
        ):
            from contextpulse_sight.app import ContextPulseSightApp

            app = ContextPulseSightApp()
            app.buffer = RollingBuffer()

            app.do_quick_capture()
            app.do_all_capture()
            app.do_region_capture()

            # All three output files exist
            assert (output_dir / "screen_latest.png").exists()
            assert (output_dir / "screen_all.png").exists()
            assert (output_dir / "screen_region.png").exists()

            # Buffer got the quick capture frame
            assert app.buffer.frame_count() >= 1

    def test_paused_state_blocks_captures(self, tmp_path):
        """Captures are skipped when the app is paused."""
        buf_dir = tmp_path / "buffer"
        output_dir = tmp_path / "screenshots"
        output_dir.mkdir()

        with (
            patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir),
            patch("contextpulse_sight.config.OUTPUT_DIR", output_dir),
            patch("contextpulse_sight.app.FILE_LATEST", output_dir / "screen_latest.png"),
            patch("contextpulse_sight.capture.capture_active_monitor", return_value=_make_image()),
        ):
            from contextpulse_sight.app import ContextPulseSightApp

            app = ContextPulseSightApp()
            app.buffer = RollingBuffer()
            app.paused = True

            app.do_quick_capture()
            assert app.buffer.frame_count() == 0

    def test_buffer_pruning_removes_old_frames(self, tmp_path):
        """Frames older than BUFFER_MAX_AGE are pruned."""
        buf_dir = tmp_path / "buffer"

        with (
            patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir),
            patch("contextpulse_sight.buffer.BUFFER_MAX_AGE", 1),  # 1 second
        ):
            buf = RollingBuffer()
            buf.add(_make_image(color=(10, 10, 10)))
            assert buf.frame_count() == 1

            # Create an artificially old frame
            old_ts = int((time.time() - 10) * 1000)  # 10 seconds ago
            old_path = buf_dir / f"{old_ts}.jpg"
            _make_image().save(old_path, format="JPEG")

            # Pruning happens on next add
            buf.add(_make_different_image())
            # Old frame should be pruned
            remaining = buf.list_frames()
            timestamps = [int(f.stem) for f in remaining]
            assert old_ts not in timestamps
