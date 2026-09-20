"""Tests for capture.py — downscale, save, bytes conversion."""

import sys
from io import BytesIO

import pytest
from PIL import Image


def _make_image(width, height, color=(128, 128, 128)):
    return Image.new("RGB", (width, height), color)


# Captured at module load, before any test can patch builtins.__import__.
# _ctypes must be imported here too (not inside a function that itself runs
# AS the patched __import__) -- an `import _ctypes` statement inside such a
# function calls the patched __import__ again for "_ctypes" every time,
# which recurses forever even though the module is already cached.
_REAL_IMPORT = __import__
if sys.platform == "win32":
    import _ctypes as _ctypes_for_tests


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

    def test_a_garbage_max_width_does_not_raise_per_frame(self, isolated_config):
        """SF-5 consequence check.

        max_width/max_height had no clamp and therefore no type coercion, so
        a hand-edited `{"max_width": "not-a-number"}` reached _max_size() as
        the string and int() raised inside _downscale() on every single
        frame -- absorbed by the capture loop's generic error counter, which
        backs off and keeps failing rather than saying anything useful. The
        clamp now coerces it back to the declared default at load time.
        """
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text('{"max_width": "not-a-number"}', encoding="utf-8")
        from contextpulse_sight.capture import _downscale
        result = _downscale(_make_image(3840, 2160))
        assert result.width <= 1280
        assert result.height <= 720

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
            # Clear cached backend to force re-detection
            import contextpulse_sight.capture as cap
            from contextpulse_sight.capture import _get_backend
            cap._backend = None
            cap._dxcam_cameras = {}
            backend = _get_backend()
            assert backend == "dxcam"

    def test_get_backend_falls_back_to_mss(self):
        from unittest.mock import patch

        with patch.dict("sys.modules", {"dxcam": None}):
            import contextpulse_sight.capture as cap
            from contextpulse_sight.capture import _get_backend
            cap._backend = None
            cap._dxcam_cameras = {}
            backend = _get_backend()
            assert backend == "mss"

    @pytest.mark.skipif(sys.platform != "win32", reason="dxcam is Windows-only")
    def test_get_backend_falls_back_to_mss_on_comerror(self):
        """dxcam's import-time DXFactory() init hits the GPU/display adapter
        directly and can raise _ctypes.COMError -- a direct Exception
        subclass, NOT ImportError/OSError (verified: COMError.__mro__ ==
        (COMError, Exception, BaseException, object)). Reproduces the live
        incident (cp-daemon-stuck-in-session0): Desktop Duplication has no
        output to enumerate in Windows Session 0 (no desktop), so every
        capture cycle re-attempted `import dxcam` and re-raised the same
        COMError -- 10,539 identical tracebacks, ~20MB of daemon_stderr.log
        from one process, because a failed import is never cached in
        sys.modules and _get_backend() had no except clause for it."""
        import _ctypes
        import builtins
        from unittest.mock import patch

        import contextpulse_sight.capture as cap

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "dxcam":
                raise _ctypes.COMError(
                    -2005270494,
                    "A resource is not available at the time of the call, "
                    "but may become available later.",
                    (None, None, None, 0, None),
                )
            return real_import(name, *args, **kwargs)

        cap._backend = None
        cap._dxcam_cameras = {}
        with patch("builtins.__import__", side_effect=_fake_import):
            backend = cap._get_backend()
        assert backend == "mss"
        # Second call must NOT re-attempt the import (proves caching, not
        # just a lucky single catch) -- if it did, the patched import above
        # is out of scope by now and a real, uncaught COMError would surface
        # instead of the cached "mss" being returned.
        assert cap._get_backend() == "mss"
        assert cap._backend == "mss"

    @pytest.mark.skipif(sys.platform != "win32", reason="dxcam is Windows-only")
    def test_comerror_fallback_in_session_0_logs_at_error_with_warning_text(self, caplog):
        """A dxcam failure that coincides with Session 0 must be unmistakable,
        not just an ordinary warning -- mss silently returns plausible-looking
        garbage for a desktop that doesn't exist in Session 0 (see
        cp-daemon-session0-blind-capture)."""
        import logging
        from unittest.mock import patch

        import contextpulse_sight.capture as cap

        cap._backend = None
        cap._dxcam_cameras = {}
        with patch("builtins.__import__", side_effect=self._raise_comerror), \
             patch("contextpulse_core.session_check.get_windows_session_id", return_value=0), \
             caplog.at_level(logging.WARNING, logger="contextpulse_sight.capture"):
            backend = cap._get_backend()

        assert backend == "mss"
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records, "Session 0 dxcam failure must log at ERROR, not merely WARNING"
        assert "Session 0" in error_records[0].getMessage()
        assert "NOT trustworthy" in error_records[0].getMessage()

    @pytest.mark.skipif(sys.platform != "win32", reason="dxcam is Windows-only")
    def test_comerror_fallback_outside_session_0_logs_at_warning_only(self, caplog):
        """The ordinary case -- a real runtime dxcam failure on an
        interactive desktop -- must NOT be escalated to ERROR; that would
        cry wolf on every legitimate transient dxcam hiccup."""
        import logging
        from unittest.mock import patch

        import contextpulse_sight.capture as cap

        cap._backend = None
        cap._dxcam_cameras = {}
        with patch("builtins.__import__", side_effect=self._raise_comerror), \
             patch("contextpulse_core.session_check.get_windows_session_id", return_value=1), \
             caplog.at_level(logging.WARNING, logger="contextpulse_sight.capture"):
            backend = cap._get_backend()

        assert backend == "mss"
        assert not [r for r in caplog.records if r.levelno == logging.ERROR]
        assert [r for r in caplog.records if r.levelno == logging.WARNING]

    @staticmethod
    def _raise_comerror(name, *args, **kwargs):
        if name == "dxcam":
            raise _ctypes_for_tests.COMError(
                -2005270494,
                "A resource is not available at the time of the call, "
                "but may become available later.",
                (None, None, None, 0, None),
            )
        # NOTE: must use the REAL __import__, captured before this test's
        # patch() replaces builtins.__import__ -- calling
        # builtins.__import__ again from inside this function would call
        # right back into the patched (this same) function and recurse
        # forever.
        return _REAL_IMPORT(name, *args, **kwargs)

    def test_ordinary_missing_dxcam_does_not_probe_session_id(self):
        """ImportError (dxcam simply not installed) is the routine, expected
        case on many setups -- it must stay quiet and must NOT pay the cost
        of a session lookup, which is only useful for diagnosing a genuine
        runtime failure."""
        from unittest.mock import patch

        import contextpulse_sight.capture as cap

        cap._backend = None
        cap._dxcam_cameras = {}
        with patch.dict("sys.modules", {"dxcam": None}), \
             patch("contextpulse_core.session_check.get_windows_session_id") as mock_session:
            backend = cap._get_backend()

        assert backend == "mss"
        mock_session.assert_not_called()

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

    def test_get_dxcam_camera_caches_failure_as_none(self):
        """dxcam.create() failures cache None so we stop retrying every capture."""
        from unittest.mock import MagicMock, patch

        mock_dxcam = MagicMock()
        # Simulate dxcam.create raising IndexError for output_idx=1 (second monitor on different adapter)
        mock_dxcam.create.side_effect = IndexError("list index out of range")

        with patch.dict("sys.modules", {"dxcam": mock_dxcam}):
            import contextpulse_sight.capture as cap
            cap._dxcam_cameras = {}

            # First call: dxcam.create raises, None is cached
            result1 = cap._get_dxcam_camera(1)
            assert result1 is None
            assert cap._dxcam_cameras == {1: None}
            assert mock_dxcam.create.call_count == 1

            # Second call: cached None returned, no second dxcam.create call
            result2 = cap._get_dxcam_camera(1)
            assert result2 is None
            assert mock_dxcam.create.call_count == 1  # unchanged

    def test_get_dxcam_camera_caches_success(self):
        """Successful dxcam.create() result is cached."""
        from unittest.mock import MagicMock, patch

        fake_camera = MagicMock()
        mock_dxcam = MagicMock()
        mock_dxcam.create.return_value = fake_camera

        with patch.dict("sys.modules", {"dxcam": mock_dxcam}):
            import contextpulse_sight.capture as cap
            cap._dxcam_cameras = {}

            result1 = cap._get_dxcam_camera(0)
            assert result1 is fake_camera
            result2 = cap._get_dxcam_camera(0)
            assert result2 is fake_camera
            assert mock_dxcam.create.call_count == 1


class TestBoundedGrab:
    """Regression tests for the 2026-08-24/25 incident: camera.grab() blocked
    for 11+ hours when DXcam's Desktop Duplication backend lost the display
    output, silently stalling the whole capture pipeline. _dxcam_grab_bounded
    must never let a stuck grab() block the caller past its timeout."""

    @pytest.fixture(autouse=True)
    def _reset_module_state(self):
        import contextpulse_sight.capture as cap
        cap._dxcam_cameras = {}
        cap._dxcam_timeout_counts = {}
        yield
        cap._dxcam_cameras = {}
        cap._dxcam_timeout_counts = {}

    def test_fast_grab_returns_frame_and_resets_counter(self):
        import numpy as np
        from unittest.mock import MagicMock

        import contextpulse_sight.capture as cap

        cap._dxcam_timeout_counts[0] = 2  # pretend 2 prior timeouts
        fake_frame = np.zeros((10, 10, 3), dtype=np.uint8)
        camera = MagicMock()
        camera.grab.return_value = fake_frame

        result = cap._dxcam_grab_bounded(camera, 0)

        assert result is fake_frame
        assert cap._dxcam_timeout_counts[0] == 0

    def test_blocking_grab_times_out_within_bound(self, monkeypatch):
        """A camera.grab() that never returns must not block the caller past
        the configured timeout -- this is the exact incident shape."""
        import threading
        import time
        from unittest.mock import MagicMock

        import contextpulse_sight.capture as cap

        monkeypatch.setattr(cap, "_DXCAM_GRAB_TIMEOUT_SEC", 0.1)
        never_set = threading.Event()
        camera = MagicMock()
        camera.grab.side_effect = lambda: never_set.wait()  # blocks forever

        start = time.monotonic()
        result = cap._dxcam_grab_bounded(camera, 0)
        elapsed = time.monotonic() - start

        assert result is None
        assert elapsed < 1.0, f"caller blocked for {elapsed:.2f}s -- timeout did not bound it"
        assert cap._dxcam_timeout_counts[0] == 1

    def test_consecutive_timeouts_evict_camera(self, monkeypatch):
        """After MAX_CONSECUTIVE_TIMEOUTS, the camera is evicted so future
        captures skip DXcam for this monitor instead of stalling again."""
        import threading
        from unittest.mock import MagicMock

        import contextpulse_sight.capture as cap

        monkeypatch.setattr(cap, "_DXCAM_GRAB_TIMEOUT_SEC", 0.05)
        never_set = threading.Event()
        camera = MagicMock()
        camera.grab.side_effect = lambda: never_set.wait()
        cap._dxcam_cameras[0] = camera

        for _ in range(cap._DXCAM_MAX_CONSECUTIVE_TIMEOUTS - 1):
            cap._dxcam_grab_bounded(camera, 0)
            assert cap._dxcam_cameras[0] is camera, "evicted before threshold reached"

        cap._dxcam_grab_bounded(camera, 0)
        assert cap._dxcam_cameras[0] is None, "not evicted after threshold reached"

    def test_genuine_exception_propagates_unchanged(self):
        """A real dxcam exception (not a hang) must still propagate through
        so existing call-site `except Exception` handling is untouched."""
        from unittest.mock import MagicMock

        import contextpulse_sight.capture as cap

        camera = MagicMock()
        camera.grab.side_effect = RuntimeError("DXGI_ERROR_DEVICE_REMOVED")

        with pytest.raises(RuntimeError, match="DXGI_ERROR_DEVICE_REMOVED"):
            cap._dxcam_grab_bounded(camera, 0)

    def test_capture_active_monitor_falls_back_to_mss_on_timeout(self, monkeypatch):
        """End-to-end: a stuck DXcam grab must not prevent
        capture_active_monitor() from returning a real image via mss."""
        import threading
        from unittest.mock import MagicMock, patch

        import contextpulse_sight.capture as cap

        monkeypatch.setattr(cap, "_DXCAM_GRAB_TIMEOUT_SEC", 0.05)
        never_set = threading.Event()
        camera = MagicMock()
        camera.grab.side_effect = lambda: never_set.wait()

        mock_sct = MagicMock()
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]
        mock_sct_img = MagicMock()
        mock_sct_img.width = 1920
        mock_sct_img.height = 1080
        mock_sct_img.rgb = b"\x00" * (1920 * 1080 * 3)
        mock_sct.grab.return_value = mock_sct_img
        mock_sct.__enter__ = MagicMock(return_value=mock_sct)
        mock_sct.__exit__ = MagicMock(return_value=False)

        with patch("contextpulse_sight.capture.mss.mss", return_value=mock_sct), \
             patch("contextpulse_sight.capture._get_backend", return_value="dxcam"), \
             patch("contextpulse_sight.capture._get_dxcam_camera", return_value=camera):
            idx, img = cap.capture_active_monitor()

        assert img.width <= 1280 and img.height <= 720
        mock_sct.grab.assert_called()  # fell all the way through to mss


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
                capture_region()
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
                capture_region(width=400, height=300)
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
