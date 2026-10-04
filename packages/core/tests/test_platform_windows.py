"""Tests for the Windows platform provider.

Regression coverage for the 2026-08-06 silent clipboard-capture failure: the
Win32 clipboard prototypes were undeclared, so ctypes defaulted their restype to
c_int (32-bit signed). GetClipboardData/GlobalLock return 64-bit values on
Win64, so the high bits were truncated and sign-extended -- a real pointer of
0x267C6C5ED40 came back as 0xFFFFFFFFA803D660 and wstring_at faulted with an
access violation. The bare `except Exception: return None` swallowed the fault,
so the clipboard monitor logged "started", captured nothing for 19 days, and
emitted no error.
"""

import ctypes
import os
import sys
from collections.abc import Mapping

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-only tests"
)


def _may_touch_shared_clipboard(env: Mapping[str, str]) -> bool:
    """Whether a test may write to the REAL system clipboard.

    The roundtrip test below is the only end-to-end proof that a clipboard
    pointer deref succeeds, so it must keep running on the Windows CI runner
    (GitHub sets CI=true) -- nothing else is using that clipboard. On a dev box
    it is a different test: the live ContextPulse daemon polls the clipboard,
    so OpenClipboard contends and the test flakes, and the daemon's monitor
    records the sentinel into the user's own clipboard history. There it runs
    only on the same explicit opt-in the live-desktop acceptance harness uses.
    """
    return env.get("CI") == "true" or env.get("CONTEXTPULSE_ACCEPTANCE") == "1"


@pytest.fixture
def provider():
    from contextpulse_core.platform.windows import WindowsPlatformProvider

    return WindowsPlatformProvider()


class TestClipboardPrototypes:
    """The prototypes must be wide enough to hold a 64-bit handle/pointer."""

    @pytest.mark.parametrize(
        ("dll_attr", "func_name"),
        [
            ("_u32", "GetClipboardData"),
            ("_k32", "GlobalLock"),
        ],
    )
    def test_handle_returning_calls_are_pointer_width(self, dll_attr, func_name):
        from contextpulse_core.platform import windows

        func = getattr(getattr(windows, dll_attr), func_name)
        assert func.restype is not None, (
            f"{func_name} has no restype; ctypes would default to 32-bit c_int "
            f"and truncate the handle"
        )
        assert ctypes.sizeof(func.restype) == ctypes.sizeof(ctypes.c_void_p), (
            f"{func_name} restype is {ctypes.sizeof(func.restype)} bytes, "
            f"needs {ctypes.sizeof(ctypes.c_void_p)} to hold a 64-bit value"
        )

    def test_c_int_restype_would_corrupt_a_real_pointer(self):
        """Anchor: show the failure the restype guard prevents.

        0x267C6C5ED40 is an actual GlobalLock result observed on this machine
        once the restype was declared. Routed through a 32-bit signed return it
        collapses to 0xFFFFFFFFC6C5ED40 -- a bogus address of exactly the shape
        that produced the observed access violation. This test fails only if
        pointers stop being wider than c_int, which would make the guard above
        meaningless.
        """
        observed_ptr = 0x267C6C5ED40
        truncated = ctypes.c_int(observed_ptr & 0xFFFFFFFF).value
        sign_extended = truncated & 0xFFFFFFFFFFFFFFFF

        assert observed_ptr > 0xFFFFFFFF, "pointer must exceed 32 bits to matter"
        assert truncated < 0, "low word has the high bit set, so it sign-extends"
        assert sign_extended != observed_ptr
        assert sign_extended == 0xFFFFFFFFC6C5ED40

    def test_argtypes_declared_for_clipboard_calls(self):
        from contextpulse_core.platform import windows

        for dll, name in [
            (windows._u32, "GetClipboardData"),
            (windows._u32, "IsClipboardFormatAvailable"),
            (windows._k32, "GlobalLock"),
            (windows._k32, "GlobalUnlock"),
        ]:
            assert getattr(dll, name).argtypes is not None, f"{name} argtypes undeclared"


class TestClipboard:
    def test_clipboard_sequence_returns_int(self, provider):
        seq = provider.get_clipboard_sequence()
        assert isinstance(seq, int)
        assert seq >= 0

    def test_clipboard_text_returns_str_or_none(self, provider):
        """Must not raise -- the old code faulted internally and masked it."""
        text = provider.get_clipboard_text()
        assert text is None or isinstance(text, str)

    @pytest.mark.skipif(
        not _may_touch_shared_clipboard(os.environ),
        reason="writes the real system clipboard, which a live ContextPulse "
        "daemon is polling; runs on CI, or opt in with CONTEXTPULSE_ACCEPTANCE=1",
    )
    def test_clipboard_read_roundtrip(self, provider):
        """Write a known value, read it back, then restore the prior text.

        This is the end-to-end check the prototype assertions cannot make: it
        proves an actual pointer deref succeeds. Skipped if the clipboard holds
        non-text data we would be unable to restore.
        """
        CF_UNICODETEXT = 13
        u32 = ctypes.windll.user32
        if not u32.OpenClipboard(None):
            pytest.skip("clipboard unavailable (locked by another process)")
        try:
            has_other_formats = False
            fmt = 0
            while True:
                fmt = u32.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                if fmt not in (CF_UNICODETEXT, 1, 7, 16):  # text-ish formats
                    has_other_formats = True
        finally:
            u32.CloseClipboard()
        if has_other_formats:
            pytest.skip("clipboard holds non-text data; refusing to clobber it")

        original = provider.get_clipboard_text()
        sentinel = "contextpulse-roundtrip-●-\U0001f9ea"  # non-ASCII + astral
        try:
            _set_clipboard_text(sentinel)
            assert provider.get_clipboard_text() == sentinel
        finally:
            if original is not None:
                _set_clipboard_text(original)


def _set_clipboard_text(text: str) -> None:
    """Minimal CF_UNICODETEXT writer used only to drive the roundtrip test."""
    GMEM_MOVEABLE = 0x0002
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    k32.GlobalAlloc.restype = ctypes.wintypes.HGLOBAL
    k32.GlobalLock.restype = ctypes.wintypes.LPVOID
    k32.GlobalLock.argtypes = [ctypes.wintypes.HGLOBAL]
    u32.SetClipboardData.argtypes = [ctypes.wintypes.UINT, ctypes.wintypes.HANDLE]
    u32.SetClipboardData.restype = ctypes.wintypes.HANDLE

    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)
    handle = k32.GlobalAlloc(GMEM_MOVEABLE, size)
    ptr = k32.GlobalLock(handle)
    ctypes.memmove(ptr, buf, size)
    k32.GlobalUnlock(handle)

    if not u32.OpenClipboard(None):
        raise RuntimeError("OpenClipboard failed")
    try:
        u32.EmptyClipboard()
        u32.SetClipboardData(13, handle)  # ownership transfers to the clipboard
    finally:
        u32.CloseClipboard()


class TestSharedClipboardGate:
    """The gate decides whether the roundtrip test may touch the real clipboard."""

    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            ({"CI": "true"}, True),  # GitHub Actions runner
            ({"CONTEXTPULSE_ACCEPTANCE": "1"}, True),  # explicit dev-box opt-in
            ({}, False),  # dev box, default: the live daemon owns the clipboard
            ({"CI": ""}, False),
            ({"CI": "false"}, False),
            ({"CONTEXTPULSE_ACCEPTANCE": "0"}, False),
        ],
    )
    def test_gate(self, env, expected):
        assert _may_touch_shared_clipboard(env) is expected
