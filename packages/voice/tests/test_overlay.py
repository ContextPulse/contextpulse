"""Tests for RecordingOverlay — state transitions with mocked tkinter."""

import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock tkinter before any import of overlay.py
# ---------------------------------------------------------------------------

_mock_tk_root = MagicMock()
_mock_tk_root.winfo_screenwidth.return_value = 1920
_mock_tk_root.winfo_pointerxy.return_value = (960, 540)

_mock_tk_module = MagicMock()
_mock_tk_module.Tk.return_value = _mock_tk_root
_mock_tk_module.Label.return_value = MagicMock()
_mock_tk_module.Frame.return_value = MagicMock()

# Patch before importing
sys.modules.setdefault("tkinter", _mock_tk_module)

# Also mock PIL.ImageTk since it requires a display
_mock_imagetk = MagicMock()
_mock_imagetk.PhotoImage = MagicMock(return_value=MagicMock())
sys.modules.setdefault("PIL.ImageTk", _mock_imagetk)

# Ensure PIL.Image and PIL.ImageDraw are available (usually installed)
try:
    from PIL import Image, ImageDraw  # noqa: F401
except ImportError:
    sys.modules.setdefault("PIL", MagicMock())
    sys.modules.setdefault("PIL.Image", MagicMock())
    sys.modules.setdefault("PIL.ImageDraw", MagicMock())


# ---------------------------------------------------------------------------
# Fixture: overlay with thread blocked (no real tkinter mainloop)
# ---------------------------------------------------------------------------

@pytest.fixture
def overlay():
    """Create a RecordingOverlay with tkinter and threading mocked out."""
    with patch("contextpulse_voice.overlay.threading") as mock_threading, \
         patch("contextpulse_voice.overlay.ImageTk", _mock_imagetk):

        # Make _ready_event set immediately (no real thread)
        mock_event = MagicMock()
        mock_event.wait.return_value = True

        mock_thread_instance = MagicMock()
        mock_threading.Event.return_value = mock_event
        mock_threading.Thread.return_value = mock_thread_instance

        from contextpulse_voice.overlay import RecordingOverlay
        ov = RecordingOverlay.__new__(RecordingOverlay)

        # Manually initialize to avoid spawning real thread
        ov._root = _mock_tk_root
        ov._visible = False
        ov._animating = False
        ov._frame_idx = 0
        ov._recording_frames = [MagicMock() for _ in range(16)]
        ov._ready_frame = MagicMock()
        ov._hide_after_id = None
        ov._ready_event = mock_event

        # Set up label mocks
        ov._icon_label = MagicMock()
        ov._text_label = MagicMock()
        ov._container = MagicMock()

        # Make root.after return an ID without calling the function.
        # Calling fn immediately causes infinite recursion in _animate() since
        # it reschedules itself. Tests call the private _*_ui() methods directly
        # to avoid going through the after() dispatch path.
        ov._root.after = MagicMock(return_value="after_id_mock")

        yield ov


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

class TestRecordingOverlayInstantiation:
    def test_can_create_overlay(self, overlay):
        assert overlay is not None

    def test_initial_visible_state_is_false(self, overlay):
        assert overlay._visible is False

    def test_initial_animating_state_is_false(self, overlay):
        assert overlay._animating is False

    def test_recording_frames_pre_rendered(self, overlay):
        assert len(overlay._recording_frames) == 16

    def test_ready_frame_exists(self, overlay):
        assert overlay._ready_frame is not None

    def test_hide_after_id_none_on_init(self, overlay):
        assert overlay._hide_after_id is None

    def test_frame_idx_starts_at_zero(self, overlay):
        assert overlay._frame_idx == 0


# ---------------------------------------------------------------------------
# show_recording state transition
# ---------------------------------------------------------------------------

class TestShowRecording:
    def test_show_recording_sets_visible(self, overlay):
        overlay._show_recording_ui()
        assert overlay._visible is True

    def test_show_recording_sets_animating(self, overlay):
        overlay._show_recording_ui()
        assert overlay._animating is True

    def test_show_recording_resets_frame_idx(self, overlay):
        # _show_recording_ui resets to 0, then _animate() advances it to 1.
        # Patch _animate to verify the reset value without side-effects.
        overlay._frame_idx = 10
        with patch.object(overlay, "_animate"):
            overlay._show_recording_ui()
        assert overlay._frame_idx == 0

    def test_show_recording_sets_red_text(self, overlay):
        overlay._show_recording_ui()
        overlay._text_label.configure.assert_called_with(text="Recording...", fg="#ef4444")

    def test_show_recording_deiconifies_root(self, overlay):
        overlay._show_recording_ui()
        overlay._root.deiconify.assert_called()

    def test_show_recording_cancels_pending_hide(self, overlay):
        # Set a pending hide ID
        overlay._hide_after_id = "pending_id"
        overlay._root.after_cancel = MagicMock()
        overlay._show_recording_ui()
        overlay._root.after_cancel.assert_called_once_with("pending_id")
        assert overlay._hide_after_id is None

    def test_show_recording_noop_when_root_is_none(self, overlay):
        overlay._root = None
        # Should not raise
        overlay.show_recording()


# ---------------------------------------------------------------------------
# show_transcribing state transition
# ---------------------------------------------------------------------------

class TestShowTranscribing:
    def test_show_transcribing_sets_visible(self, overlay):
        overlay._show_transcribing_ui()
        assert overlay._visible is True

    def test_show_transcribing_stops_animation(self, overlay):
        overlay._animating = True
        overlay._show_transcribing_ui()
        assert overlay._animating is False

    def test_show_transcribing_sets_yellow_text(self, overlay):
        overlay._show_transcribing_ui()
        overlay._text_label.configure.assert_called_with(text="Transcribing...", fg="#facc15")

    def test_show_transcribing_uses_ready_frame(self, overlay):
        overlay._show_transcribing_ui()
        overlay._icon_label.configure.assert_called_with(image=overlay._ready_frame)

    def test_show_transcribing_deiconifies_root(self, overlay):
        overlay._show_transcribing_ui()
        overlay._root.deiconify.assert_called()

    def test_show_transcribing_noop_when_root_is_none(self, overlay):
        overlay._root = None
        overlay.show_transcribing()  # Should not raise


# ---------------------------------------------------------------------------
# show_ready state transition
# ---------------------------------------------------------------------------

class TestShowReady:
    def test_show_ready_sets_visible(self, overlay):
        overlay._show_ready_ui()
        assert overlay._visible is True

    def test_show_ready_stops_animation(self, overlay):
        overlay._animating = True
        overlay._show_ready_ui()
        assert overlay._animating is False

    def test_show_ready_sets_green_text(self, overlay):
        overlay._show_ready_ui()
        overlay._text_label.configure.assert_called_with(text="Ready", fg="#4ade80")

    def test_show_ready_uses_ready_frame(self, overlay):
        overlay._show_ready_ui()
        overlay._icon_label.configure.assert_called_with(image=overlay._ready_frame)

    def test_show_ready_schedules_hide(self, overlay):
        """show_ready should schedule a hide after 1500ms."""
        after_calls = []
        def _after_capture(delay, fn=None, *args):
            after_calls.append((delay, fn))
            return "after_id_123"
        overlay._root.after = _after_capture

        overlay._show_ready_ui()
        # Should schedule a hide at 1500ms
        delays = [d for d, _ in after_calls]
        assert 1500 in delays

    def test_show_ready_noop_when_root_is_none(self, overlay):
        overlay._root = None
        overlay.show_ready()  # Should not raise


# ---------------------------------------------------------------------------
# hide state transition
# ---------------------------------------------------------------------------

class TestHide:
    def test_hide_sets_visible_false(self, overlay):
        overlay._visible = True
        overlay._hide_ui()
        assert overlay._visible is False

    def test_hide_stops_animation(self, overlay):
        overlay._animating = True
        overlay._hide_ui()
        assert overlay._animating is False

    def test_hide_withdraws_root(self, overlay):
        overlay._hide_ui()
        overlay._root.withdraw.assert_called()

    def test_hide_noop_when_root_is_none(self, overlay):
        overlay._root = None
        overlay.hide()  # Should not raise


# ---------------------------------------------------------------------------
# State machine: full recording → transcribing → ready → hide sequence
# ---------------------------------------------------------------------------

class TestStateSequence:
    def test_recording_to_transcribing(self, overlay):
        overlay._show_recording_ui()
        assert overlay._visible is True
        assert overlay._animating is True

        overlay._show_transcribing_ui()
        assert overlay._visible is True
        assert overlay._animating is False

    def test_transcribing_to_ready(self, overlay):
        overlay._show_transcribing_ui()
        overlay._show_ready_ui()
        assert overlay._visible is True
        assert overlay._animating is False
        overlay._text_label.configure.assert_called_with(text="Ready", fg="#4ade80")

    def test_ready_to_hidden(self, overlay):
        overlay._show_ready_ui()
        overlay._hide_ui()
        assert overlay._visible is False

    def test_second_recording_cancels_hide_timer(self, overlay):
        """Starting a new recording while 'Ready' timer is pending cancels it."""
        overlay._hide_after_id = "timer_123"
        overlay._root.after_cancel = MagicMock()

        overlay._show_recording_ui()

        overlay._root.after_cancel.assert_called_once_with("timer_123")
        assert overlay._hide_after_id is None
