"""Tests for privacy controls: window title blocklist and session lock."""

from unittest.mock import patch, MagicMock

from contextpulse_sight import privacy


class TestIsBlocked:
    """Test window title blocklist matching."""

    def test_empty_blocklist_never_blocks(self):
        with patch.object(privacy, "BLOCKLIST_PATTERNS", []):
            assert privacy.is_blocked() is False

    def test_matching_title_blocks(self):
        with (
            patch.object(privacy, "BLOCKLIST_PATTERNS", ["1Password", "Bank"]),
            patch.object(privacy, "get_foreground_window_title", return_value="1Password - Login"),
        ):
            assert privacy.is_blocked() is True

    def test_non_matching_title_allows(self):
        with (
            patch.object(privacy, "BLOCKLIST_PATTERNS", ["1Password", "Bank"]),
            patch.object(privacy, "get_foreground_window_title", return_value="Visual Studio Code"),
        ):
            assert privacy.is_blocked() is False

    def test_case_insensitive_match(self):
        with (
            patch.object(privacy, "BLOCKLIST_PATTERNS", ["bank"]),
            patch.object(privacy, "get_foreground_window_title", return_value="My BANK Account"),
        ):
            assert privacy.is_blocked() is True

    def test_substring_match(self):
        with (
            patch.object(privacy, "BLOCKLIST_PATTERNS", ["Private"]),
            patch.object(privacy, "get_foreground_window_title", return_value="Chrome - Private Browsing"),
        ):
            assert privacy.is_blocked() is True

    def test_multiple_patterns_any_match(self):
        with (
            patch.object(privacy, "BLOCKLIST_PATTERNS", ["Secret", "Password", "Bank"]),
            patch.object(privacy, "get_foreground_window_title", return_value="Online Banking - Chase"),
        ):
            assert privacy.is_blocked() is True

    def test_empty_title_no_match(self):
        with (
            patch.object(privacy, "BLOCKLIST_PATTERNS", ["Bank"]),
            patch.object(privacy, "get_foreground_window_title", return_value=""),
        ):
            assert privacy.is_blocked() is False


class TestGetForegroundWindowTitle:
    """Test Win32 API wrapper for window title."""

    def test_returns_string(self):
        title = privacy.get_foreground_window_title()
        assert isinstance(title, str)


class TestSessionMonitor:
    """Test session lock/unlock monitor."""

    def test_creates_with_callbacks(self):
        on_lock = MagicMock()
        on_unlock = MagicMock()
        monitor = privacy.SessionMonitor(on_lock=on_lock, on_unlock=on_unlock)
        assert monitor.on_lock is on_lock
        assert monitor.on_unlock is on_unlock

    def test_start_launches_daemon_thread(self):
        monitor = privacy.SessionMonitor(on_lock=lambda: None, on_unlock=lambda: None)
        assert monitor._thread.daemon is True


class TestAppPrivacyIntegration:
    """Test that app.py correctly uses privacy controls."""

    def test_blocked_window_skips_capture(self, tmp_path):
        buf_dir = tmp_path / "buffer"

        with (
            patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir),
            patch("contextpulse_sight.app.is_blocked", return_value=True),
            patch("contextpulse_sight.capture.capture_active_monitor") as mock_capture,
        ):
            from contextpulse_sight.app import ContextPulseSightApp
            from contextpulse_sight.buffer import RollingBuffer

            app = ContextPulseSightApp()
            app.buffer = RollingBuffer()
            app.do_quick_capture()

            mock_capture.assert_not_called()
            assert app.buffer.frame_count() == 0

    def test_allowed_window_permits_capture(self, tmp_path):
        buf_dir = tmp_path / "buffer"
        output_dir = tmp_path / "screenshots"
        output_dir.mkdir()

        from PIL import Image
        test_img = Image.new("RGB", (100, 100), (128, 128, 128))

        with (
            patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir),
            patch("contextpulse_sight.app.is_blocked", return_value=False),
            patch("contextpulse_sight.app.FILE_LATEST", output_dir / "screen_latest.png"),
            patch("contextpulse_sight.capture.capture_active_monitor", return_value=(0, test_img)),
        ):
            from contextpulse_sight.app import ContextPulseSightApp
            from contextpulse_sight.buffer import RollingBuffer

            app = ContextPulseSightApp()
            app.buffer = RollingBuffer()
            app.do_quick_capture()

            assert app.buffer.frame_count() == 1

    def test_session_lock_auto_pauses(self, tmp_path):
        buf_dir = tmp_path / "buffer"

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            from contextpulse_sight.app import ContextPulseSightApp

            app = ContextPulseSightApp()
            assert app.paused is False

            app._on_session_lock()
            assert app.paused is True

            app._on_session_unlock()
            assert app.paused is False

    def test_user_pause_preserved_across_lock_unlock(self, tmp_path):
        buf_dir = tmp_path / "buffer"

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            from contextpulse_sight.app import ContextPulseSightApp

            app = ContextPulseSightApp()

            # User manually pauses
            app.toggle_pause()
            assert app.paused is True
            assert app._user_paused is True

            # Session locks (already paused)
            app._on_session_lock()
            assert app.paused is True

            # Session unlocks — should stay paused because user paused
            app._on_session_unlock()
            assert app.paused is True

    def test_no_user_pause_resumes_after_unlock(self, tmp_path):
        buf_dir = tmp_path / "buffer"

        with patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir):
            from contextpulse_sight.app import ContextPulseSightApp

            app = ContextPulseSightApp()
            assert app._user_paused is False

            # Session locks
            app._on_session_lock()
            assert app.paused is True

            # Session unlocks — should resume because user didn't pause
            app._on_session_unlock()
            assert app.paused is False
