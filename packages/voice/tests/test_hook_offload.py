# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Tests for pynput hook-thread offload.

Acceptance criteria (from feat/voice-audio-persistence-orphan-recovery,
hook-thread offload phase):

  - _on_press_inner / _on_release_inner do NOT directly call
    Recorder.start / .stop / .stop_after_silence, transcriber.transcribe,
    or threading.Thread(target=...).
  - Hook callbacks return in <50ms even when the worker thread is busy.
  - Commands enqueued from hook callbacks are processed in order.
  - Worker thread survives exceptions in individual commands.
  - stop() drains the queue and joins the worker within 5s.
  - is_alive() reflects worker health.
  - Queue overflow drops oldest with a warning rather than blocking.

These tests fail today — the offload does not exist yet.
"""

from __future__ import annotations

import inspect
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from pynput import keyboard as kb

# ── Source-inspection tests ───────────────────────────────────────────


class TestCallbacksAreNonBlocking:
    """Hook callbacks must not call blocking APIs directly.

    Background: Windows enforces a `LowLevelHooksTimeout` of 5000ms.
    If the pynput hook callback blocks longer (e.g. while the GIL is
    held by a long Whisper transcribe), Windows silently unhooks the
    listener — pystray then unwinds and the daemon exits cleanly.
    Plus the in-flight key state is left dangling, which surfaces as
    a "stuck key" symptom on the user's keyboard.
    """

    def test_on_press_inner_does_not_call_recorder_directly(self):
        from contextpulse_voice.voice_module import VoiceModule

        src = inspect.getsource(VoiceModule._on_press_inner)
        forbidden = [
            "self._recorder.start",
            "self._recorder.stop",
            "self._recorder.stop_after_silence",
        ]
        for token in forbidden:
            assert token not in src, (
                f"_on_press_inner contains blocking call '{token}'. "
                "Move recorder operations to the worker thread "
                "(enqueue _VoiceCommand instead)."
            )

    def test_on_release_inner_does_not_call_recorder_or_transcribe(self):
        from contextpulse_voice.voice_module import VoiceModule

        src = inspect.getsource(VoiceModule._on_release_inner)
        forbidden = [
            "self._recorder.start",
            "self._recorder.stop",
            "self._recorder.stop_after_silence",
            "self._transcriber.transcribe",
            "self._transcribe_and_paste",
            "self._stop_and_transcribe",
        ]
        for token in forbidden:
            assert token not in src, (
                f"_on_release_inner contains blocking call '{token}'. "
                "Move heavy work to the worker thread."
            )

    def test_callbacks_do_not_spawn_threads(self):
        """Spawning a Thread on the hook callback path is itself
        a Win32 syscall. Use the persistent worker queue instead."""
        from contextpulse_voice.voice_module import VoiceModule

        for name in ("_on_press_inner", "_on_release_inner"):
            src = inspect.getsource(getattr(VoiceModule, name))
            assert "threading.Thread" not in src, (
                f"{name} spawns a Thread directly. Enqueue a "
                "_VoiceCommand on self._command_queue instead."
            )

    def test_callbacks_do_not_sleep(self):
        """Sleeping in the hook callback is the canonical bug —
        guarded by source inspection."""
        from contextpulse_voice.voice_module import VoiceModule

        for name in ("_on_press_inner", "_on_release_inner"):
            src = inspect.getsource(getattr(VoiceModule, name))
            assert "time.sleep" not in src, f"{name} calls time.sleep — blocks the listener thread."


# ── Behavior tests with a live worker ─────────────────────────────────


@pytest.fixture
def live_module():
    """VoiceModule with mocked hardware but a REAL worker thread."""
    with patch("contextpulse_voice.voice_module.get_voice_config") as mock_cfg:
        mock_cfg.return_value = {
            "hotkey": "ctrl+space",
            "fix_hotkey": "ctrl+shift+space",
            "whisper_model": "base",
            "always_use_llm": False,
            "anthropic_api_key": "",
        }
        from contextpulse_voice.voice_module import VoiceModule

        m = VoiceModule(model_size="base")
        m._recorder = MagicMock()
        m._recorder.stop_after_silence.return_value = b"\x00" * 1024
        m._transcriber = MagicMock()
        m._transcriber.transcribe.return_value = "hello"
        m._callback = MagicMock()
        m._running = True

        # Spin up worker the same way start() will
        m._start_worker()
        yield m
        # Best-effort teardown so leaked workers don't stack across tests
        try:
            m._stop_worker(timeout=2.0)
        except Exception:
            pass


class TestHookCallbackLatency:
    def test_press_callback_returns_quickly_when_worker_blocked(self, live_module):
        """Even if the worker is stuck on a long transcribe, hook
        callbacks must return in <50ms — the queue absorbs the work."""
        # Block the worker artificially
        worker_busy = threading.Event()
        worker_release = threading.Event()

        def _slow_start(*_, **__):
            worker_busy.set()
            worker_release.wait(timeout=5)

        live_module._recorder.start.side_effect = _slow_start

        # Trigger one START to occupy the worker
        live_module._on_press_inner(kb.Key.ctrl_l)
        live_module._on_press_inner(kb.Key.space)

        # Wait until worker is actually busy
        assert worker_busy.wait(timeout=2), "Worker never picked up the START command"

        # Now time a fresh callback while worker is blocked. Use
        # _on_release_inner — _on_press_inner has a guard that returns
        # immediately if _recording is already True.
        start_t = time.perf_counter()
        live_module._on_release_inner(kb.Key.space)
        elapsed_ms = (time.perf_counter() - start_t) * 1000

        worker_release.set()  # let worker finish

        assert elapsed_ms < 50, f"Hook callback took {elapsed_ms:.1f}ms (limit 50ms)"


class TestCommandOrdering:
    def test_press_release_sequence_preserved(self, live_module):
        """Commands must be processed in the order they were enqueued."""
        seen: list[str] = []

        live_module._recorder.start.side_effect = lambda *_, **__: seen.append("start")
        live_module._recorder.stop_after_silence.side_effect = lambda *_, **__: (
            seen.append("stop"),
            b"\x00" * 1024,
        )[1]
        # Make transcribe a no-op so the worker drains quickly
        live_module._transcriber.transcribe.return_value = ""

        live_module._on_press_inner(kb.Key.ctrl_l)
        live_module._on_press_inner(kb.Key.space)
        time.sleep(0.05)
        live_module._on_release_inner(kb.Key.space)

        # Wait for queue to drain
        deadline = time.time() + 3
        while time.time() < deadline:
            if seen == ["start", "stop"]:
                break
            time.sleep(0.05)

        assert seen == ["start", "stop"], f"Order was {seen!r}"


class TestWorkerSurvival:
    def test_worker_survives_command_exception(self, live_module):
        """A broken handler must not kill the worker."""
        # First START explodes
        call_count = {"n": 0}

        def _flaky(*_, **__):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("first call boom")

        live_module._recorder.start.side_effect = _flaky

        # First press: worker hits exception
        live_module._on_press_inner(kb.Key.ctrl_l)
        live_module._on_press_inner(kb.Key.space)
        time.sleep(0.3)

        # Worker must still be alive
        assert live_module._worker is not None
        assert live_module._worker.is_alive(), "Worker died after one bad command"

        # And subsequent commands still process
        live_module._recording = False  # reset state
        live_module._last_audio_hash = None
        live_module._on_press_inner(kb.Key.space)
        time.sleep(0.3)
        # Either second press scheduled a new start, or the existing
        # _recording flag prevented re-entry — both are acceptable.
        # The point is the worker is still alive.
        assert live_module._worker.is_alive()


class TestShutdown:
    def test_stop_drains_and_joins_worker(self, live_module):
        """stop() must enqueue a sentinel and join the worker promptly."""
        # Verify worker is up
        assert live_module._worker is not None
        assert live_module._worker.is_alive()

        live_module._stop_worker(timeout=5.0)

        assert not live_module._worker.is_alive(), "Worker did not exit within 5s of shutdown"

    def test_is_alive_false_when_worker_dead(self, live_module):
        """is_alive() must reflect worker health, not just the
        _running flag — otherwise the daemon watchdog can't restart us."""
        live_module._stop_worker(timeout=5.0)
        # _running stays True (only stop() clears it), but worker is dead
        assert not live_module.is_alive(), (
            "is_alive() returned True even though worker thread is dead"
        )


class TestRecordingTimeoutWatchdog:
    """If pynput drops a release event (known Windows hook failure
    mode), the daemon would otherwise stay stuck thinking recording
    is active — recorder keeps capturing, overlay stuck on "recording",
    no transcription. The worker periodically checks recording duration
    and force-enqueues a STOP if recording has run past the cap.
    """

    def test_long_recording_auto_stops_after_cap(self, live_module):
        """When recording exceeds the duration cap, the worker
        auto-enqueues a STOP without needing a key release."""
        # Patch the duration cap to a small value for fast testing.
        # The watchdog should trip after CAP + grace period (~5s).
        import contextpulse_voice.voice_module as vm

        original_cap = vm._MAX_RECORDING_S
        original_grace = vm._RECORDING_TIMEOUT_GRACE_S
        try:
            vm._MAX_RECORDING_S = 0.5  # 500ms cap
            vm._RECORDING_TIMEOUT_GRACE_S = 0.2  # 200ms grace

            # Simulate user pressing ctrl+space (recording starts)
            live_module._on_press_inner(kb.Key.ctrl_l)
            live_module._on_press_inner(kb.Key.space)

            # Wait for the worker to handle START
            deadline = time.time() + 2
            while time.time() < deadline and not live_module._recorder.start.called:
                time.sleep(0.05)
            assert live_module._recorder.start.called, "Recorder.start was never called"

            # Now WITHOUT releasing the keys, wait past cap + grace.
            # Watchdog should auto-enqueue a STOP.
            deadline = time.time() + 3
            while time.time() < deadline:
                if live_module._recorder.stop_after_silence.called:
                    break
                time.sleep(0.1)

            assert live_module._recorder.stop_after_silence.called, (
                "Worker did not auto-stop the recording after duration cap "
                "(stuck-release safety net failed)"
            )
        finally:
            vm._MAX_RECORDING_S = original_cap
            vm._RECORDING_TIMEOUT_GRACE_S = original_grace

    def test_normal_recording_not_auto_stopped(self, live_module):
        """Recordings within the cap must NOT be auto-stopped."""
        live_module._on_press_inner(kb.Key.ctrl_l)
        live_module._on_press_inner(kb.Key.space)
        time.sleep(0.2)
        # Recording started but only 0.2s in — far below 60s cap.
        # User releases normally:
        live_module._on_release_inner(kb.Key.space)
        # Wait long enough for worker to process STOP (includes the
        # 700ms tail-buffer sleep inside _stop_and_transcribe).
        deadline = time.time() + 3
        while time.time() < deadline:
            if live_module._recorder.stop_after_silence.called:
                break
            time.sleep(0.05)

        # stop_after_silence WAS called once (from the user release),
        # not from a watchdog auto-stop.
        assert live_module._recorder.stop_after_silence.call_count == 1

    def test_handle_stop_clears_recording_timestamp(self, live_module):
        """After a normal stop processes, the recording timestamp is
        cleared so the watchdog doesn't fire on the NEXT idle cycle."""
        live_module._on_press_inner(kb.Key.ctrl_l)
        live_module._on_press_inner(kb.Key.space)
        time.sleep(0.2)
        live_module._on_release_inner(kb.Key.space)
        # Wait for stop pipeline to finish (tail buffer + transcribe)
        deadline = time.time() + 3
        while time.time() < deadline:
            if live_module._recording_started_at is None:
                break
            time.sleep(0.05)

        # After stop processes, _recording_started_at is None
        assert live_module._recording_started_at is None


class TestQueueOverflow:
    def test_overflow_drops_oldest_with_warning(self, live_module, caplog):
        """A burst of commands must not block the hook thread.

        With a bounded queue, when full the implementation should drop
        the oldest pending command and log a WARNING — never block the
        hook callback waiting for queue space.
        """
        # Block the worker so queue fills
        gate = threading.Event()
        live_module._recorder.start.side_effect = lambda *_, **__: gate.wait(timeout=3)

        # Fill the queue
        for _ in range(20):  # well above maxsize
            live_module._enqueue_test("start")

        # Hook thread did not block — we got here.
        # Drain
        gate.set()
        time.sleep(0.5)

        # At least one overflow warning was logged
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "queue" in r.message.lower() or "overflow" in r.message.lower() for r in warnings
        ), "Expected a queue-overflow warning"
