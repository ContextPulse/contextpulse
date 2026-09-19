"""Tests for Windows Session 0 detection (contextpulse_core.session_check).

Session 0 is the non-interactive services session -- see the module
docstring for the incident (cp-daemon-session0-blind-capture) this exists
to catch. These tests mock ctypes.windll.kernel32.ProcessIdToSessionId
directly so the suite is deterministic regardless of which session it
actually runs in.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest
from contextpulse_core import session_check

# ---------------------------------------------------------------------------
# get_windows_session_id
# ---------------------------------------------------------------------------


class TestGetWindowsSessionId:
    def test_returns_none_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(session_check.sys, "platform", "darwin")
        assert session_check.get_windows_session_id() is None

    def test_returns_none_on_linux(self, monkeypatch):
        monkeypatch.setattr(session_check.sys, "platform", "linux")
        assert session_check.get_windows_session_id() is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_returns_session_0_when_win32_reports_zero(self, monkeypatch):
        monkeypatch.setattr(session_check.sys, "platform", "win32")

        def _fake_p2s(pid, ptr):
            ptr.contents.value = 0
            return 1  # nonzero = success

        fake_kernel32 = MagicMock()
        fake_kernel32.ProcessIdToSessionId.side_effect = _fake_p2s
        with patch.object(session_check.ctypes, "windll", MagicMock(kernel32=fake_kernel32)):
            assert session_check.get_windows_session_id(pid=1234) == 0

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_returns_positive_session_when_interactive(self, monkeypatch):
        monkeypatch.setattr(session_check.sys, "platform", "win32")

        def _fake_p2s(pid, ptr):
            ptr.contents.value = 1
            return 1

        fake_kernel32 = MagicMock()
        fake_kernel32.ProcessIdToSessionId.side_effect = _fake_p2s
        with patch.object(session_check.ctypes, "windll", MagicMock(kernel32=fake_kernel32)):
            assert session_check.get_windows_session_id(pid=1234) == 1

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_returns_none_when_win32_call_fails(self, monkeypatch):
        monkeypatch.setattr(session_check.sys, "platform", "win32")
        fake_kernel32 = MagicMock()
        fake_kernel32.ProcessIdToSessionId.return_value = 0  # 0 = failure per Win32 convention
        fake_kernel32.GetLastError.return_value = 5  # ERROR_ACCESS_DENIED
        with patch.object(session_check.ctypes, "windll", MagicMock(kernel32=fake_kernel32)):
            assert session_check.get_windows_session_id(pid=1234) is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_returns_none_when_win32_call_raises(self, monkeypatch):
        monkeypatch.setattr(session_check.sys, "platform", "win32")
        fake_kernel32 = MagicMock()
        fake_kernel32.ProcessIdToSessionId.side_effect = OSError("boom")
        with patch.object(session_check.ctypes, "windll", MagicMock(kernel32=fake_kernel32)):
            assert session_check.get_windows_session_id(pid=1234) is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_live_call_returns_an_int_or_none(self):
        """Sanity check against the REAL Win32 API for this process.

        Not asserting a specific session id (that depends on how the test
        runner was launched) -- only that the real call path doesn't raise
        and returns a plausible type.
        """
        result = session_check.get_windows_session_id()
        assert result is None or isinstance(result, int)


# ---------------------------------------------------------------------------
# is_session_0
# ---------------------------------------------------------------------------


class TestIsSession0:
    def test_true_when_session_id_is_zero(self, monkeypatch):
        monkeypatch.setattr(session_check, "get_windows_session_id", lambda pid=None: 0)
        assert session_check.is_session_0() is True

    def test_false_when_session_id_is_positive(self, monkeypatch):
        monkeypatch.setattr(session_check, "get_windows_session_id", lambda pid=None: 1)
        assert session_check.is_session_0() is False

    def test_false_when_session_id_is_none(self, monkeypatch):
        """Undeterminable is NOT the same claim as 'proven to be Session 0' --
        see the docstring for why the daemon startup guard must NOT reuse
        this helper's fail-open default."""
        monkeypatch.setattr(session_check, "get_windows_session_id", lambda pid=None: None)
        assert session_check.is_session_0() is False
