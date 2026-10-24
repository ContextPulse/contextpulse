"""The daemon watchdog must not restart a voice module that is still starting.

contextpulse.log, 2 of 29 daemon starts: "Voice module died -- restarting
(attempt 1/3)" about 15 s after start while the first Whisper load was still
running, then "Whisper model loaded" and "VoiceModule started" twice each. The
first module was never stopped, so two keyboard listeners served the dictation
hotkey until the next restart.

These tests run one real watchdog tick (stop_event faked, OUTPUT_DIR pointed at
tmp_path so the heartbeat never touches the live screenshots folder).
"""

import itertools
import sys
import types
from unittest.mock import MagicMock, patch

import contextpulse_core.daemon as daemon_mod
import pytest

from tests.test_daemon import _make_daemon  # noqa: E402  (shared factory)


def _one_tick(daemon, tmp_path, ticks=1, clock_step=None):
    """Run _watchdog_loop for `ticks` ticks; return the VoiceModule class mock.

    clock_step: when set, time.time() inside the daemon returns 0, step, 2*step...
    The heartbeat also calls time.time() each tick, so one tick advances the
    clock by at least 2*step; "step" is not elapsed time per tick.
    """
    stop = MagicMock(name="stop_event")
    # per tick: while-check, post-wait check; then a final while-check exits
    stop.is_set.side_effect = [False, False] * ticks + [True]
    daemon.stop_event = stop
    daemon._sight_app = None
    daemon._touch_module = None
    voice_cls = MagicMock(name="VoiceModule")
    # A stub module, not patch("contextpulse_voice.voice_module.VoiceModule"):
    # importing the real module here pulls in pyautogui, which fails under this
    # package's tkinter mock when the file runs on its own.
    fake_mod = types.ModuleType("contextpulse_voice.voice_module")
    fake_mod.VoiceModule = voice_cls
    with patch.object(daemon_mod, "OUTPUT_DIR", tmp_path), \
         patch.dict(sys.modules, {"contextpulse_voice.voice_module": fake_mod}):
        if clock_step is None:
            daemon._watchdog_loop()
        else:
            with patch.object(daemon_mod.time, "time", side_effect=itertools.count(0, clock_step)):
                daemon._watchdog_loop()
    return voice_cls


@pytest.fixture
def daemon_and_voice(tmp_path):
    daemon, mocks = _make_daemon(tmp_path)
    return daemon, mocks["voice"]


def test_voice_still_starting_is_not_restarted(daemon_and_voice, tmp_path):
    daemon, voice = daemon_and_voice
    voice.is_alive.return_value = False
    voice.is_starting.return_value = True

    voice_cls = _one_tick(daemon, tmp_path)

    voice_cls.assert_not_called()
    assert daemon._voice_module is voice
    assert daemon._restart_counts.get("voice", 0) == 0


def test_voice_that_died_after_starting_is_restarted(daemon_and_voice, tmp_path):
    daemon, voice = daemon_and_voice
    voice.is_alive.return_value = False
    voice.is_starting.return_value = False

    voice_cls = _one_tick(daemon, tmp_path)

    voice_cls.assert_called_once()
    assert daemon._voice_module is voice_cls.return_value
    assert daemon._restart_counts["voice"] == 1


def test_voice_without_is_starting_still_restarts(daemon_and_voice, tmp_path):
    """A module type that predates is_starting() keeps the old behaviour."""
    daemon, voice = daemon_and_voice
    voice.is_alive.return_value = False
    del voice.is_starting

    voice_cls = _one_tick(daemon, tmp_path)

    voice_cls.assert_called_once()


def test_start_stuck_past_limit_is_reported_once_and_not_restarted(daemon_and_voice, tmp_path):
    """A start that never returns must not wait in silence (reviewer, 2026-10-20)."""
    daemon, voice = daemon_and_voice
    voice.is_alive.return_value = False
    voice.is_starting.return_value = True

    with patch.object(daemon, "_notify_tray") as notify:
        voice_cls = _one_tick(daemon, tmp_path, ticks=4, clock_step=400)

    voice_cls.assert_not_called()
    assert notify.call_count == 1
    assert "not starting" in notify.call_args[0][0]


def test_slow_start_under_limit_is_not_reported(daemon_and_voice, tmp_path):
    daemon, voice = daemon_and_voice
    voice.is_alive.return_value = False
    voice.is_starting.return_value = True

    with patch.object(daemon, "_notify_tray") as notify:
        voice_cls = _one_tick(daemon, tmp_path, ticks=3, clock_step=50)

    voice_cls.assert_not_called()
    notify.assert_not_called()
