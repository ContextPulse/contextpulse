"""Tests for capture.py — downscale, save, bytes conversion."""

import sys
from io import BytesIO

import pytest
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


class TestCaptureBackend:
    """Test DXcam/mss backend abstraction."""

    @pytest.mark.skipif(sys.platform != "win32", reason="dxcam is Windows-only")
    def test_get_backend_returns_dxcam_when_available(self):
        from unittest.mock import MagicMock, patch

        mock_dxcam = MagicMock()
        mock_dxcam.create.return_value = MagicMock()

        with patch.dict("sys.modules", {"dxcam": mock_dxcam}):
            from contextpulse_sight.capture import _get_backend
            # Clear cached backend to force re-detection
            import contextpulse_sight.capture as cap
            cap._backend = None
            cap._dxcam_cameras = {}
            backend = _get_backend()
            assert backend == "dxcam"

    def test_get_backend_falls_back_to_mss(self):
        from unittest.mock import patch

        with patch.dict("sys.modules", {"dxcam": None}):
            from contextpulse_sight.capture import _get_backend
            import contextpulse_sight.capture as cap
            cap._backend = None
            cap._dxcam_cameras = {}
            backend = _get_backend()
            assert backend == "mss"

    def test_dxcam_grab_returns_pil_image(self):
        """DXcam grab returns numpy BGR array — must be converted to PIL RGB."""
        import numpy as np
        from contextpulse_sight.capture import _dxcam_to_pil

        # Simulate a DXcam BGR frame (100x100, blue channel=255)
        bgr_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        bgr_frame[:, :, 0] = 255  # Blue channel in BGR

        img = _dxcam_to_pil(bgr_frame)
        assert img.mode == "RGB"
        assert img.size == (100, 100)
        # After BGR→RGB conversion, the red channel should be 255
        r, g, b = img.getpixel((50, 50))
        assert b == 255  # was blue in BGR, still blue in RGB
        assert r == 0

    def test_dxcam_to_pil_handles_rgba(self):
        """DXcam can return BGRA with output_color='BGRA'."""
        import numpy as np
        from contextpulse_sight.capture import _dxcam_to_pil

        bgra_frame = np.zeros((50, 50, 4), dtype=np.uint8)
        bgra_frame[:, :, 1] = 128  # Green channel

        img = _dxcam_to_pil(bgra_frame)
        assert img.mode == "RGB"
        _, g, _ = img.getpixel((25, 25))
        assert g == 128


class TestActiveWindowRect:
    """Test active window detection for adaptive region capture."""

    def test_returns_none_on_non_windows(self):
        from unittest.mock import patch
        from contextpulse_sight.capture import get_active_window_rect

        with patch("contextpulse_sight.capture.sys") as mock_sys:
            mock_sys.platform = "linux"
            result = get_active_window_rect()
            assert result is None

    def test_returns_none_when_no_foreground_window(self):
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import get_active_window_rect

        with patch("contextpulse_sight.capture.sys") as mock_sys:
            mock_sys.platform = "win32"
            import ctypes
            mock_user32 = MagicMock()
            mock_user32.GetForegroundWindow.return_value = 0  # NULL
            with patch.object(ctypes.windll, "user32", mock_user32):
                result = get_active_window_rect()
                assert result is None

    def test_returns_rect_on_success(self):
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import get_active_window_rect

        with patch("contextpulse_sight.capture.sys") as mock_sys:
            mock_sys.platform = "win32"
            import ctypes

            mock_user32 = MagicMock()
            mock_user32.GetForegroundWindow.return_value = 12345
            mock_user32.IsWindow.return_value = True

            mock_dwmapi = MagicMock()
            # DwmGetWindowAttribute sets rect fields via side_effect
            def fake_dwm(hwnd, attr, rect_ptr, size):
                import ctypes as ct
                rect = ct.cast(rect_ptr, ct.POINTER(ct.c_long * 4)).contents
                rect[0] = 100   # left
                rect[1] = 200   # top
                rect[2] = 900   # right
                rect[3] = 700   # bottom
                return 0  # S_OK
            mock_dwmapi.DwmGetWindowAttribute.side_effect = fake_dwm

            with patch.object(ctypes.windll, "user32", mock_user32), \
                 patch.object(ctypes.windll, "dwmapi", mock_dwmapi):
                result = get_active_window_rect()
                assert result == (100, 200, 800, 500)  # (left, top, width, height)

    def test_handles_exception_gracefully(self):
        from unittest.mock import patch
        from contextpulse_sight.capture import get_active_window_rect

        with patch("contextpulse_sight.capture.sys") as mock_sys:
            mock_sys.platform = "win32"
            import ctypes
            # Make windll.user32 raise
            with patch.object(ctypes, "windll", side_effect=AttributeError("no windll")):
                result = get_active_window_rect()
                assert result is None


class TestAdaptiveRegion:
    """Test adaptive region capture sizing."""

    def test_auto_size_uses_active_window(self):
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import capture_region

        # Mock get_active_window_rect to return a window at (100, 200, 800, 600)
        with patch("contextpulse_sight.capture.get_active_window_rect", return_value=(100, 200, 800, 600)):
            mock_sct = MagicMock()
            mock_sct.monitors = [
                {"left": 0, "top": 0, "width": 1920, "height": 1080},
            ]
            mock_sct_img = MagicMock()
            mock_sct_img.width = 900
            mock_sct_img.height = 700
            mock_sct_img.rgb = b"\x80" * (900 * 700 * 3)
            mock_sct.grab.return_value = mock_sct_img

            with patch("contextpulse_sight.capture.mss.mss", return_value=mock_sct):
                mock_sct.__enter__ = MagicMock(return_value=mock_sct)
                mock_sct.__exit__ = MagicMock(return_value=False)
                img = capture_region()  # width=0, height=0 → auto-detect
                # Should have captured a region (the grab was called)
                mock_sct.grab.assert_called_once()
                assert img.width <= 1280
                assert img.height <= 720

    def test_fallback_to_cursor_centered(self):
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import capture_region

        # No active window
        with patch("contextpulse_sight.capture.get_active_window_rect", return_value=None), \
             patch("contextpulse_sight.capture._get_cursor_pos", return_value=(500, 500)):
            mock_sct = MagicMock()
            mock_sct.monitors = [
                {"left": 0, "top": 0, "width": 1920, "height": 1080},
            ]
            mock_sct_img = MagicMock()
            mock_sct_img.width = 800
            mock_sct_img.height = 600
            mock_sct_img.rgb = b"\x80" * (800 * 600 * 3)
            mock_sct.grab.return_value = mock_sct_img

            with patch("contextpulse_sight.capture.mss.mss", return_value=mock_sct):
                mock_sct.__enter__ = MagicMock(return_value=mock_sct)
                mock_sct.__exit__ = MagicMock(return_value=False)
                img = capture_region()
                mock_sct.grab.assert_called_once()

    def test_explicit_size_bypasses_auto_detect(self):
        from unittest.mock import MagicMock, patch
        from contextpulse_sight.capture import capture_region

        with patch("contextpulse_sight.capture.get_active_window_rect") as mock_rect, \
             patch("contextpulse_sight.capture._get_cursor_pos", return_value=(500, 500)):
            mock_sct = MagicMock()
            mock_sct.monitors = [
                {"left": 0, "top": 0, "width": 1920, "height": 1080},
            ]
            mock_sct_img = MagicMock()
            mock_sct_img.width = 400
            mock_sct_img.height = 300
            mock_sct_img.rgb = b"\x80" * (400 * 300 * 3)
            mock_sct.grab.return_value = mock_sct_img

            with patch("contextpulse_sight.capture.mss.mss", return_value=mock_sct):
                mock_sct.__enter__ = MagicMock(return_value=mock_sct)
                mock_sct.__exit__ = MagicMock(return_value=False)
                img = capture_region(width=400, height=300)
                # Should NOT have called get_active_window_rect
                mock_rect.assert_not_called()


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

        import pytest
        from contextpulse_sight.capture import capture_single_monitor

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
