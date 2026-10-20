"""VoiceModule reports a start that is still loading the Whisper model.

Why this exists: the daemon starts voice on a background thread, and start()
only sets ``_running`` after the Whisper model has loaded. When that load ran
past the watchdog's first 15 s tick, the watchdog read is_alive() == False,
logged "Voice module died -- restarting" and built a second VoiceModule while
the first was still loading. Both then finished and both started a keyboard
listener on the dictation hotkey. contextpulse.log shows it on 2 of 29 daemon
starts (17:12:13 and 20:17:34, each followed by "VoiceModule started" twice).

is_starting() lets the watchdog tell "still loading" from "died".
"""

import threading
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def module():
    with patch("contextpulse_voice.voice_module.get_voice_config") as mock_cfg:
        mock_cfg.return_value = {
            "hotkey": "ctrl+space",
            "fix_hotkey": "ctrl+shift+space",
            "whisper_model": "base",
            "always_use_llm": False,
            "anthropic_api_key": "",
        }
        from contextpulse_voice.voice_module import VoiceModule

        yield VoiceModule(model_size="base")


def _start_patches(transcriber_side_effect):
    """Patch every heavy dependency start() touches; return the Listener mock."""
    listener_cls = MagicMock(name="Listener")
    return listener_cls, [
        patch("contextpulse_voice.voice_module.Recorder"),
        patch("contextpulse_voice.transcriber.LocalTranscriber", side_effect=transcriber_side_effect),
        patch("contextpulse_voice.overlay.RecordingOverlay"),
        patch("contextpulse_voice.voice_module.kb.Listener", listener_cls),
        patch("contextpulse_voice.voice_module.set_drop_notifier"),
    ]


def _run_start(module, transcriber_side_effect):
    listener_cls, patches = _start_patches(transcriber_side_effect)
    for p in patches:
        p.start()
    try:
        module.start()
    finally:
        for p in reversed(patches):
            p.stop()
    return listener_cls


def test_is_starting_false_before_start(module):
    assert module.is_starting() is False
    assert module.is_alive() is False


def test_is_starting_true_while_model_loads_and_false_after(module):
    seen = {}

    def _loading(**_kwargs):
        seen["starting"] = module.is_starting()
        seen["alive"] = module.is_alive()
        return MagicMock(name="transcriber")

    _run_start(module, _loading)

    assert seen == {"starting": True, "alive": False}
    assert module.is_starting() is False
    assert module.is_alive() is True


def test_is_starting_cleared_when_model_load_fails(module):
    def _boom(**_kwargs):
        raise RuntimeError("model load failed")

    with pytest.raises(RuntimeError):
        _run_start(module, _boom)

    assert module.is_starting() is False
    assert module.is_alive() is False


def test_second_start_during_load_does_not_add_a_listener(module):
    """A start() call that lands while the first is loading must be a no-op."""
    second_returned = threading.Event()
    calls = []

    def _loading(**_kwargs):
        calls.append(1)
        if len(calls) == 1:  # only the outer start() fires the second one
            t = threading.Thread(target=lambda: (module.start(), second_returned.set()))
            t.start()
            t.join(timeout=5)
        return MagicMock(name="transcriber")

    listener_cls = _run_start(module, _loading)

    assert second_returned.is_set(), "second start() blocked or raised"
    assert listener_cls.call_count == 1
