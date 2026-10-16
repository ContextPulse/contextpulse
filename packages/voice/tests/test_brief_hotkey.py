"""Brief-mode hotkey: ctrl+alt+space contains ctrl+space, so only the brief path may start,
one brief runs at a time, and the dictation is stored before the model is called."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from contextpulse_voice.brief import BriefOutcome
from pynput import keyboard as kb

CFG = {
    "hotkey": "ctrl+space",
    "fix_hotkey": "ctrl+shift+space",
    "whisper_model": "base",
    "always_use_llm": False,
    "anthropic_api_key": "",
    "brief_hotkey": "ctrl+alt+space",
    "brief_template": "brief",
    "llm_backend": "auto",
}


@pytest.fixture
def vm():
    with patch("contextpulse_voice.voice_module.get_voice_config", return_value=dict(CFG)):
        from contextpulse_voice.voice_module import VoiceModule

        m = VoiceModule(model_size="base")
        m._recorder = MagicMock()
        m._recorder.stop_after_silence.return_value = b"\x01" * 3200
        m._transcriber = MagicMock()
        m._transcriber.transcribe.return_value = "please fix the flag and do not push"
        m._callback = MagicMock()
        m._running = True
        m._TAIL_BUFFER_MS = 0
        yield m


def press(m, *keys):
    for k in keys:
        m._on_press_inner(k)


class TestBriefHotkeyStarts:
    def test_ctrl_alt_space_starts_brief_not_dictation(self, vm):
        press(vm, kb.Key.ctrl_l, kb.Key.alt_l, kb.Key.space)
        assert vm._recording is True
        assert vm._record_mode == "brief"
        assert vm._briefing is True
        assert vm._recorder.start.call_count == 1, "exactly one recording, not two"

    def test_right_alt_also_works(self, vm):
        press(vm, kb.Key.ctrl_r, kb.Key.alt_r, kb.Key.space)
        assert vm._record_mode == "brief"

    def test_ctrl_space_still_dictates(self, vm):
        press(vm, kb.Key.ctrl_l, kb.Key.space)
        assert vm._recording is True
        assert vm._record_mode == "dictation"
        assert vm._briefing is False

    def test_fix_last_hotkey_does_not_start_a_brief(self, vm):
        vm._last_wav_bytes = None
        press(vm, kb.Key.ctrl_l, kb.Key.shift_l, kb.Key.space)
        assert vm._briefing is False

    def test_no_brief_hotkey_configured_means_feature_off(self):
        cfg = {k: v for k, v in CFG.items() if k != "brief_hotkey"}
        with patch("contextpulse_voice.voice_module.get_voice_config", return_value=cfg):
            from contextpulse_voice.voice_module import VoiceModule

            m = VoiceModule(model_size="base")
        m._recorder = MagicMock()
        m._running = True
        press(m, kb.Key.ctrl_l, kb.Key.alt_l, kb.Key.space)
        assert m._record_mode == "dictation", "without a brief hotkey ctrl+alt+space is just dictation"
        assert m._briefing is False

    def test_recorder_start_failure_releases_every_flag(self, vm):
        vm._recorder.start.side_effect = OSError("device gone")
        press(vm, kb.Key.ctrl_l, kb.Key.alt_l, kb.Key.space)
        assert (vm._recording, vm._recorder_busy, vm._briefing) == (False, False, False)


class TestOneBriefAtATime:
    def test_dictation_and_fix_last_refused_while_briefing(self, vm):
        vm._briefing = True
        vm._last_wav_bytes = b"x"
        press(vm, kb.Key.ctrl_l, kb.Key.space)
        assert vm._recording is False, "dictation must not start during a brief"
        vm._pressed_keys.clear()
        press(vm, kb.Key.ctrl_l, kb.Key.shift_l, kb.Key.space)
        assert vm._fixing is False, "Fix-last must not start during a brief"

    def test_second_brief_refused_while_first_runs(self, vm):
        vm._briefing = True
        press(vm, kb.Key.ctrl_l, kb.Key.alt_l, kb.Key.space)
        assert vm._recording is False


class TestBriefRelease:
    def test_releasing_alt_stops_the_brief_recording(self, vm):
        press(vm, kb.Key.ctrl_l, kb.Key.alt_l, kb.Key.space)
        with patch("threading.Thread") as T:
            vm._on_release_inner(kb.Key.alt_l)
        assert vm._recording is False
        _, kwargs = T.call_args
        assert kwargs["args"][-1] == "brief"

    def test_spawn_failure_releases_briefing(self, vm):
        press(vm, kb.Key.ctrl_l, kb.Key.alt_l, kb.Key.space)
        with patch("threading.Thread", side_effect=RuntimeError("no threads")):
            vm._on_release_inner(kb.Key.space)
        assert vm._briefing is False and vm._recorder_busy is False


class TestBriefPipeline:
    def _run(self, vm, outcome=None, raises=None):
        order = []
        vm._callback.side_effect = lambda ev: order.append(("emit", ev.payload.get("brief_template")))

        def fake_make_brief(text, template, **kw):
            order.append(("brief", template, kw.get("backend")))
            if raises:
                raise raises
            return outcome or BriefOutcome(text="Goal (delivery): X", template=template, llm_ok=True)

        with patch("contextpulse_voice.voice_module.make_brief", side_effect=fake_make_brief), \
             patch("contextpulse_voice.voice_module.paste_text") as paste, \
             patch("contextpulse_voice.voice_module.get_voice_config", return_value=dict(CFG)):
            vm._briefing = True
            vm._stop_and_transcribe("Code", "win", "brief")
        return order, paste

    def test_dictation_is_stored_before_the_model_call_and_brief_is_pasted(self, vm):
        order, paste = self._run(vm)
        assert order[0] == ("emit", "brief"), order
        assert order[1] == ("brief", "brief", "auto"), order
        paste.assert_called_once_with("Goal (delivery): X")
        assert vm._briefing is False

    def test_stored_event_holds_the_dictation_not_the_brief(self, vm):
        self._run(vm)
        payload = vm._callback.call_args_list[0].args[0].payload
        assert "do not push" in payload["transcript"].lower()
        assert payload["cleanup_applied"] is False

    def test_brief_audio_does_not_replace_fix_last_audio(self, vm):
        vm._last_wav_bytes = b"previous dictation"
        self._run(vm)
        assert vm._last_wav_bytes == b"previous dictation"

    def test_last_brief_status_records_what_ran(self, vm):
        self._run(vm, outcome=BriefOutcome(text="d", template="brief", llm_ok=False,
                                           reason="claude-cli timeout after 30s"))
        status = vm.get_status()["last_brief"]
        assert status["template"] == "brief"
        assert status["llm_ok"] is False
        assert "timeout" in status["reason"]

    def test_crash_in_brief_pastes_the_dictation_and_releases_the_flag(self, vm):
        _, paste = self._run(vm, raises=RuntimeError("bug"))
        assert vm._briefing is False
        paste.assert_called_once()
        assert "do not push" in paste.call_args.args[0].lower()
        assert "crashed" in vm.get_status()["last_brief"]["reason"]

    def test_crash_in_paste_still_releases_the_flag(self, vm):
        with patch("contextpulse_voice.voice_module.make_brief",
                   return_value=BriefOutcome(text="B", template="brief", llm_ok=True)), \
             patch("contextpulse_voice.voice_module.paste_text", side_effect=OSError("clipboard")), \
             patch("contextpulse_voice.voice_module.get_voice_config", return_value=dict(CFG)):
            vm._briefing = True
            vm._stop_and_transcribe("Code", "win", "brief")
        assert vm._briefing is False

    def test_no_audio_releases_the_flag(self, vm):
        vm._recorder.stop_after_silence.return_value = b""
        vm._briefing = True
        vm._stop_and_transcribe("Code", "win", "brief")
        assert vm._briefing is False

    def test_dictation_path_unchanged_and_never_calls_make_brief(self, vm):
        with patch("contextpulse_voice.voice_module.make_brief") as mb, \
             patch("contextpulse_voice.voice_module.paste_text") as paste, \
             patch("contextpulse_voice.voice_module.get_voice_config", return_value=dict(CFG)):
            vm._stop_and_transcribe("Code", "win")
        mb.assert_not_called()
        paste.assert_called_once()
        assert vm._last_wav_bytes is not None


class TestRealThread:
    def test_brief_runs_on_its_own_thread_and_releases(self, vm):
        """Press and release through the real handlers; the worker thread does the rest."""
        pasted = threading.Event()
        with patch("contextpulse_voice.voice_module.make_brief",
                   return_value=BriefOutcome(text="B", template="brief", llm_ok=True)), \
             patch("contextpulse_voice.voice_module.paste_text", side_effect=lambda t: pasted.set()), \
             patch("contextpulse_voice.voice_module.get_voice_config", return_value=dict(CFG)):
            press(vm, kb.Key.ctrl_l, kb.Key.alt_l, kb.Key.space)
            vm._on_release_inner(kb.Key.space)
            assert pasted.wait(5), "brief never pasted"
            deadline = time.time() + 5
            while vm._briefing and time.time() < deadline:
                time.sleep(0.01)
        assert vm._briefing is False
