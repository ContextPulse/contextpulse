"""Tests for privacy controls: window title blocklist and session lock.

Every blocklist test here sets the list the way a USER sets it -- through
``contextpulse_core.config.save_config``, the same call the Settings dialog
makes -- and then calls the real daemon-path function with nothing patched.
The previous version of this file patched
``contextpulse_sight.privacy.BLOCKLIST_PATTERNS`` directly, which is exactly
why the defect it was supposed to cover shipped: the module constant the
tests injected into was the one thing on the path that a saved setting never
reached (cp-settings-dialog-disconnected-from-daemon). A test that injects a
value never exercises the resolver that produces it.
"""

import time
from unittest.mock import MagicMock, patch

from contextpulse_core.config import save_config
from contextpulse_sight import privacy


def _with_title(title: str):
    """Patch the platform provider's foreground title, leaving privacy alone."""
    return patch.object(privacy, "get_foreground_window_title", return_value=title)


class TestIsBlocked:
    """Window title blocklist matching, driven by the saved config."""

    def test_empty_blocklist_never_blocks(self, isolated_config):
        save_config({"blocklist_patterns": []})
        with _with_title("1Password - Login"):
            assert privacy.is_blocked() is False

    def test_saved_blocklist_blocks_foreground(self, isolated_config):
        """T1: the pattern a user saves in Settings reaches is_blocked()."""
        save_config({"blocklist_patterns": ["Bank"]})
        with _with_title("Online Banking - Chase"):
            assert privacy.is_blocked() is True

    def test_non_matching_title_allows(self, isolated_config):
        save_config({"blocklist_patterns": ["1Password", "Bank"]})
        with _with_title("Visual Studio Code"):
            assert privacy.is_blocked() is False

    def test_case_insensitive_match(self, isolated_config):
        save_config({"blocklist_patterns": ["bank"]})
        with _with_title("My BANK Account"):
            assert privacy.is_blocked() is True

    def test_multiple_patterns_any_match(self, isolated_config):
        save_config({"blocklist_patterns": ["Secret", "Password", "Bank"]})
        with _with_title("Online Banking - Chase"):
            assert privacy.is_blocked() is True

    def test_empty_title_no_match(self, isolated_config):
        save_config({"blocklist_patterns": ["Bank"]})
        with _with_title(""):
            assert privacy.is_blocked() is False

    def test_env_var_replaces_the_saved_list(self, isolated_config, monkeypatch):
        """Env beats config.json, and REPLACES rather than merges (spec s3)."""
        save_config({"blocklist_patterns": ["Bank"]})
        monkeypatch.setenv("CONTEXTPULSE_BLOCKLIST", "Vault,Ledger")
        with _with_title("Online Banking - Chase"):
            assert privacy.is_blocked() is False
        with _with_title("Vault - unlocked"):
            assert privacy.is_blocked() is True


class TestDefaultBlocklist:
    """T2: the 14 shipped defaults are live behaviour, not decoration."""

    def test_default_blocklist_blocks_password_manager(self, isolated_config):
        assert privacy.is_title_blocked("1Password - Login") is True

    def test_default_blocklist_blocks_authenticator(self, isolated_config):
        assert privacy.is_title_blocked("Microsoft Authenticator") is True

    def test_default_blocklist_allows_an_ordinary_window(self, isolated_config):
        assert privacy.is_title_blocked("app.py - Visual Studio Code") is False


class TestWordBoundary:
    """T6: a pattern anchors to a word START, so it cannot match mid-word."""

    def test_sign_in_blocks_a_real_sign_in_window(self, isolated_config):
        assert privacy.is_title_blocked("Sign in to GitHub") is True

    def test_sign_in_does_not_block_design_in_figma(self, isolated_config):
        assert privacy.is_title_blocked("Design in Figma") is False

    def test_log_in_does_not_block_blog_index(self, isolated_config):
        assert privacy.is_title_blocked("Blog index") is False

    def test_a_pattern_still_matches_a_longer_word(self, isolated_config):
        """The trailing boundary is deliberately NOT enforced.

        "Bank" must keep blocking "Banking" and "Password" must keep blocking
        "Passwords" -- a user who typed the shorter form is relying on that
        today, and quietly narrowing a privacy control is the wrong direction
        to fail in.
        """
        save_config({"blocklist_patterns": ["Bank"]})
        assert privacy.is_title_blocked("Online Banking") is True
        assert privacy.is_title_blocked("Fairbanks weather") is False

    def test_a_regex_metacharacter_is_matched_literally(self, isolated_config):
        save_config({"blocklist_patterns": ["Acct (1.2)"]})
        assert privacy.is_title_blocked("Acct (1.2) - statement") is True
        assert privacy.is_title_blocked("Acct (192) - statement") is False


class TestBlocklistLiveReload:
    """T3: a Settings save changes behaviour with no restart."""

    def test_blocklist_change_takes_effect_without_reload(self, isolated_config):
        save_config({"blocklist_patterns": ["Alpha"]})
        assert privacy.is_title_blocked("Alpha console") is True
        assert privacy.is_title_blocked("Beta console") is False

        time.sleep(0.01)  # a distinct mtime; save_config also drops the cache
        save_config({"blocklist_patterns": ["Beta"]})

        assert privacy.is_title_blocked("Alpha console") is False, (
            "the old list survived a save -- the read is bound, not live"
        )
        assert privacy.is_title_blocked("Beta console") is True

    def test_the_compiled_cache_is_rebuilt_when_the_list_changes(self, isolated_config):
        """The cache must key on the list, not merely be populated once."""
        save_config({"blocklist_patterns": ["Alpha"]})
        privacy.is_title_blocked("anything")
        first = privacy._COMPILED
        assert first is not None and first[0] == ("Alpha",)

        privacy.is_title_blocked("anything else")
        assert privacy._COMPILED is first, "recompiled for an unchanged list"

        time.sleep(0.01)
        save_config({"blocklist_patterns": ["Beta"]})
        privacy.is_title_blocked("anything")
        assert privacy._COMPILED is not None
        assert privacy._COMPILED[0] == ("Beta",)


class TestBlocklistFallbacks:
    def test_a_non_list_value_is_treated_as_empty_not_a_crash(self, isolated_config):
        """A hand-edited config.json can hold anything; capture must not die."""
        with patch.object(privacy, "cfg_get", return_value="Bank"):
            assert privacy.is_title_blocked("Online Banking") is False


class TestGetForegroundWindowTitle:
    """Test Win32 API wrapper for window title."""

    def test_returns_string(self):
        title = privacy.get_foreground_window_title()
        assert isinstance(title, str)


class TestSessionMonitor:
    """Test session lock/unlock monitor factory."""

    def test_creates_monitor_via_platform_provider(self):
        on_lock = MagicMock()
        on_unlock = MagicMock()
        monitor = privacy.SessionMonitor(on_lock=on_lock, on_unlock=on_unlock)
        # SessionMonitor is now a factory that delegates to the platform provider.
        # It should return something (the platform provider's session monitor).
        assert monitor is not None

    def test_monitor_has_start_method(self):
        monitor = privacy.SessionMonitor(on_lock=lambda: None, on_unlock=lambda: None)
        assert hasattr(monitor, "start")


class TestAppPrivacyIntegration:
    """Test that app.py correctly uses privacy controls."""

    def test_saved_blocklist_skips_quick_capture(self, tmp_path, isolated_config):
        """T4: end-to-end, with is_blocked NOT patched.

        The old version of this test patched ``app.is_blocked`` to True, so it
        proved only that app.py honours a boolean -- never that a saved
        blocklist produces that boolean. That gap is the defect.
        """
        buf_dir = tmp_path / "buffer"
        save_config({"blocklist_patterns": ["Online Banking"]})

        with (
            patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir),
            patch.object(privacy, "get_foreground_window_title", return_value="Online Banking - Chase"),
            patch("contextpulse_sight.capture.capture_active_monitor") as mock_capture,
        ):
            from contextpulse_sight.app import ContextPulseSightApp
            from contextpulse_sight.buffer import RollingBuffer

            app = ContextPulseSightApp()
            app.buffer = RollingBuffer()
            app.do_quick_capture()

            mock_capture.assert_not_called()
            assert app.buffer.frame_count() == 0

    def test_allowed_window_permits_capture(self, tmp_path, isolated_config):
        buf_dir = tmp_path / "buffer"
        output_dir = tmp_path / "screenshots"
        output_dir.mkdir()
        save_config({"blocklist_patterns": ["Online Banking"]})

        from PIL import Image
        test_img = Image.new("RGB", (100, 100), (128, 128, 128))

        with (
            patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir),
            patch.object(privacy, "get_foreground_window_title", return_value="Visual Studio Code"),
            patch("contextpulse_sight.app.FILE_LATEST", output_dir / "screen_latest.jpg"),
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
