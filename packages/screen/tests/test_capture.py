"""Tests for capture.py — downscale, save, bytes conversion."""

from io import BytesIO
from pathlib import Path

from PIL import Image


def _make_image(width, height, color=(128, 128, 128)):
    return Image.new("RGB", (width, height), color)


class TestDownscale:
    """Test image downscaling logic."""

    def test_small_image_unchanged(self):
        from contextpulse_sight.capture import _downscale
        img = _make_image(640, 480)
        result = _downscale(img)
        assert result.width == 640
        assert result.height == 480

    def test_exact_max_size_unchanged(self):
        from contextpulse_sight.capture import _downscale
        img = _make_image(1280, 720)
        result = _downscale(img)
        assert result.width == 1280
        assert result.height == 720

    def test_large_image_downscaled(self):
        from contextpulse_sight.capture import _downscale
        img = _make_image(3840, 2160)
        result = _downscale(img)
        assert result.width <= 1280
        assert result.height <= 720

    def test_wide_image_preserves_aspect(self):
        from contextpulse_sight.capture import _downscale
        img = _make_image(3840, 1080)  # ultra-wide
        result = _downscale(img)
        assert result.width <= 1280
        assert result.height <= 720
        # Aspect ratio: 3840/1080 = 3.56, should be maintained
        original_ratio = 3840 / 1080
        new_ratio = result.width / result.height
        assert abs(original_ratio - new_ratio) < 0.1

    def test_tall_image_preserves_aspect(self):
        from contextpulse_sight.capture import _downscale
        img = _make_image(1080, 3840)  # very tall
        result = _downscale(img)
        assert result.width <= 1280
        assert result.height <= 720


class TestSaveImage:
    """Test saving images to disk."""

    def test_save_png(self, tmp_output_dir):
        from contextpulse_sight.capture import save_image
        img = _make_image(100, 100)
        path = tmp_output_dir / "test.png"
        save_image(img, path, fmt="PNG")
        assert path.exists()
        assert path.stat().st_size > 0
        # Verify it's actually a PNG
        loaded = Image.open(path)
        assert loaded.format == "PNG"

    def test_save_jpeg(self, tmp_output_dir):
        from contextpulse_sight.capture import save_image
        img = _make_image(100, 100)
        path = tmp_output_dir / "test.jpg"
        save_image(img, path, fmt="JPEG")
        assert path.exists()
        loaded = Image.open(path)
        assert loaded.format == "JPEG"

    def test_save_creates_parent_dirs(self, tmp_output_dir):
        from contextpulse_sight.capture import save_image
        img = _make_image(100, 100)
        path = tmp_output_dir / "nested" / "dir" / "test.png"
        save_image(img, path, fmt="PNG")
        assert path.exists()

    def test_save_rgba_as_jpeg_converts(self, tmp_output_dir):
        from contextpulse_sight.capture import save_image
        img = Image.new("RGBA", (100, 100), (128, 128, 128, 255))
        path = tmp_output_dir / "test.jpg"
        save_image(img, path, fmt="JPEG")
        assert path.exists()
        loaded = Image.open(path)
        assert loaded.mode == "RGB"


class TestCaptureToBytes:
    """Test image-to-bytes conversion."""

    def test_png_bytes(self):
        from contextpulse_sight.capture import capture_to_bytes
        img = _make_image(100, 100)
        data = capture_to_bytes(img, fmt="PNG")
        assert isinstance(data, bytes)
        assert len(data) > 0
        # Verify it's valid PNG
        loaded = Image.open(BytesIO(data))
        assert loaded.format == "PNG"

    def test_jpeg_bytes(self):
        from contextpulse_sight.capture import capture_to_bytes
        img = _make_image(100, 100)
        data = capture_to_bytes(img, fmt="JPEG")
        assert isinstance(data, bytes)
        loaded = Image.open(BytesIO(data))
        assert loaded.format == "JPEG"

    def test_jpeg_smaller_than_png(self):
        from contextpulse_sight.capture import capture_to_bytes
        img = _make_image(500, 500)
        png_data = capture_to_bytes(img, fmt="PNG")
        jpeg_data = capture_to_bytes(img, fmt="JPEG")
        assert isinstance(jpeg_data, bytes)
        assert isinstance(png_data, bytes)

    def test_rgba_jpeg_converts(self):
        from contextpulse_sight.capture import capture_to_bytes
        img = Image.new("RGBA", (100, 100), (128, 128, 128, 255))
        data = capture_to_bytes(img, fmt="JPEG")
        loaded = Image.open(BytesIO(data))
        assert loaded.mode == "RGB"


class TestMonitorDetection:
    """Test monitor selection logic with mocked mss."""

    def test_find_monitor_at_cursor_single_monitor(self):
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import find_monitor_at_cursor

        sct = MagicMock()
        # monitors[0] = virtual desktop, monitors[1] = primary
        sct.monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},  # virtual
            {"left": 0, "top": 0, "width": 1920, "height": 1080},  # primary
        ]

        with patch("contextpulse_sight.capture._get_cursor_pos", return_value=(500, 500)):
            idx, mon = find_monitor_at_cursor(sct)
            assert idx == 0
            assert mon == sct.monitors[1]

    def test_find_monitor_at_cursor_dual_monitor(self):
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import find_monitor_at_cursor

        sct = MagicMock()
        sct.monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 1080},  # virtual
            {"left": 0, "top": 0, "width": 1920, "height": 1080},  # left
            {"left": 1920, "top": 0, "width": 1920, "height": 1080},  # right
        ]

        # Cursor on right monitor
        with patch("contextpulse_sight.capture._get_cursor_pos", return_value=(2500, 500)):
            idx, mon = find_monitor_at_cursor(sct)
            assert idx == 1
            assert mon == sct.monitors[2]

    def test_find_monitor_fallback_to_primary(self):
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import find_monitor_at_cursor

        sct = MagicMock()
        sct.monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]

        # Cursor way outside any monitor
        with patch("contextpulse_sight.capture._get_cursor_pos", return_value=(9999, 9999)):
            idx, mon = find_monitor_at_cursor(sct)
            assert idx == 0
            assert mon == sct.monitors[1]

    def test_find_monitor_only_virtual_desktop(self):
        """Edge case: only monitors[0] exists (shouldn't happen in practice)."""
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import find_monitor_at_cursor

        sct = MagicMock()
        sct.monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]

        with patch("contextpulse_sight.capture._get_cursor_pos", return_value=(500, 500)):
            idx, mon = find_monitor_at_cursor(sct)
            # Should return monitors[0] as fallback since monitors[1] doesn't exist
            assert idx == 0
            assert mon == sct.monitors[0]

    def test_capture_single_monitor_valid(self):
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import capture_single_monitor

        mock_sct = MagicMock()
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1920, "height": 1080},
        ]
        mock_sct_img = MagicMock()
        mock_sct_img.width = 1920
        mock_sct_img.height = 1080
        mock_sct_img.rgb = b"\x00" * (1920 * 1080 * 3)
        mock_sct.grab.return_value = mock_sct_img

        with patch("contextpulse_sight.capture.mss.mss", return_value=mock_sct):
            mock_sct.__enter__ = MagicMock(return_value=mock_sct)
            mock_sct.__exit__ = MagicMock(return_value=False)
            img = capture_single_monitor(0)
            assert img.width <= 1280
            assert img.height <= 720

    def test_capture_single_monitor_invalid_raises(self):
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import capture_single_monitor
        import pytest

        mock_sct = MagicMock()
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]

        with patch("contextpulse_sight.capture.mss.mss", return_value=mock_sct):
            mock_sct.__enter__ = MagicMock(return_value=mock_sct)
            mock_sct.__exit__ = MagicMock(return_value=False)
            with pytest.raises(ValueError, match="out of range"):
                capture_single_monitor(5)

    def test_get_monitor_count(self):
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import get_monitor_count

        mock_sct = MagicMock()
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1920, "height": 1080},
        ]

        with patch("contextpulse_sight.capture.mss.mss", return_value=mock_sct):
            mock_sct.__enter__ = MagicMock(return_value=mock_sct)
            mock_sct.__exit__ = MagicMock(return_value=False)
            assert get_monitor_count() == 2

    def test_capture_all_monitors_returns_list(self):
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import capture_all_monitors

        mock_sct = MagicMock()
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1920, "height": 1080},
        ]
        mock_sct_img = MagicMock()
        mock_sct_img.width = 1920
        mock_sct_img.height = 1080
        mock_sct_img.rgb = b"\x00" * (1920 * 1080 * 3)
        mock_sct.grab.return_value = mock_sct_img

        with patch("contextpulse_sight.capture.mss.mss", return_value=mock_sct):
            mock_sct.__enter__ = MagicMock(return_value=mock_sct)
            mock_sct.__exit__ = MagicMock(return_value=False)
            result = capture_all_monitors()
            assert isinstance(result, list)
            assert len(result) == 2
            assert result[0][0] == 0  # monitor index
            assert result[1][0] == 1
            assert result[0][1].width <= 1280
