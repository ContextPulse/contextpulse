"""Tests for app.py — ContextPulseSightApp core capture logic.

No test coverage existed for _do_auto_capture before this file (noted in
cp-ocr-crossmonitor-mislabeled-attribution, 2026-09-13) — the OCR
cross-monitor attribution bug lived in the auto-capture loop's only
untested function.
"""

from unittest.mock import patch

from PIL import Image


def _make_image(width=100, height=100, color=(128, 128, 128)):
    return Image.new("RGB", (width, height), color)


def _make_app(tmp_path, monkeypatch):
    """Construct a real ContextPulseSightApp against tmp_path-scoped storage.

    Same pattern as test_dual_write.py's test_app_has_sight_module_and_event_bus:
    ContextPulseSightApp.__init__ is lightweight (no pystray/pynput setup), so
    it can be instantiated directly once BUFFER_DIR and ACTIVITY_DB_PATH are
    monkeypatched at their owning modules.
    """
    import contextpulse_sight.activity as act
    import contextpulse_sight.buffer as buf_mod
    import contextpulse_sight.config as cfg

    buf_dir = tmp_path / "buffer"
    buf_dir.mkdir()
    monkeypatch.setattr(cfg, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(buf_mod, "BUFFER_DIR", buf_dir)
    monkeypatch.setattr(act, "ACTIVITY_DB_PATH", tmp_path / "activity.db")

    from contextpulse_sight.app import ContextPulseSightApp
    return ContextPulseSightApp()


class TestDoAutoCaptureMonitorAttribution:
    """Regression coverage for cp-ocr-crossmonitor-mislabeled-attribution.

    _do_auto_capture reads the system-wide foreground app/window ONCE per
    capture cycle (get_foreground_process_name / get_foreground_window_title)
    and, before this fix, applied that single value identically to every
    monitor's frame when enqueuing OCR — so a non-cursor monitor's OCR text
    was permanently attributed to whatever app happened to have keyboard
    focus, even when that app was not what the OCR'd frame shows. Verified
    live (2026-09-13): 98 of 98 real events over a 9-day probe_consolidator
    stall showed near-identical taskbar/desktop OCR text attributed to three
    different foreground apps across the window.
    """

    def test_cursor_monitor_keeps_attribution_non_cursor_suppressed(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch)
        try:
            monitors = [
                (0, _make_image(), _make_image()),
                (1, _make_image(color=(50, 50, 50)), _make_image(color=(50, 50, 50))),
            ]
            enqueued: list[tuple[tuple, dict]] = []
            monkeypatch.setattr(
                app._ocr_worker, "enqueue",
                lambda *a, **kw: enqueued.append((a, kw)),
            )

            with (
                patch("contextpulse_sight.app.capture.capture_all_monitors", return_value=monitors),
                patch("contextpulse_sight.app.capture.find_monitor_at_cursor", return_value=(1, {})),
                patch("mss.mss"),
                patch("contextpulse_sight.app.get_foreground_window_title", return_value="Real Window"),
                patch("contextpulse_sight.app.get_foreground_process_name", return_value="real.exe"),
                patch("contextpulse_sight.app.is_blocked", return_value=False),
            ):
                app._do_auto_capture()

            assert len(enqueued) == 2, f"expected one enqueue call per monitor, got {enqueued}"

            by_monitor = {kwargs["monitor_index"]: (args, kwargs) for args, kwargs in enqueued}
            assert set(by_monitor) == {0, 1}, (
                f"enqueue must carry monitor_index for both monitors, got {by_monitor.keys()}"
            )

            # Cursor monitor (1): the OCR'd frame IS what the foreground app
            # shows, so the real system-wide attribution is trustworthy here.
            cursor_args, cursor_kwargs = by_monitor[1]
            assert cursor_args[2] == "real.exe"
            assert cursor_kwargs["window_title"] == "Real Window"

            # Non-cursor monitor (0): the foreground app has keyboard focus
            # on a DIFFERENT monitor, so attributing its OCR text to
            # "real.exe" / "Real Window" is false. Must be suppressed, not
            # copied from the cursor monitor's values.
            other_args, other_kwargs = by_monitor[0]
            assert other_args[2] != "real.exe", (
                "non-cursor monitor's OCR must not be attributed to the "
                "system-wide foreground app -- that app is not what this "
                "monitor's frame shows"
            )
            assert other_kwargs["window_title"] != "Real Window"
        finally:
            app._sight_module.stop()
            app._event_bus.close()
            app.activity_db.close()

    def test_single_monitor_keeps_real_attribution(self, tmp_path, monkeypatch):
        """Sanity: a single-monitor machine is always its own cursor monitor,
        so suppression must never fire there -- this is a multi-monitor-only
        defect and the fix must not regress the common case."""
        app = _make_app(tmp_path, monkeypatch)
        try:
            monitors = [(0, _make_image(), _make_image())]
            enqueued: list[tuple[tuple, dict]] = []
            monkeypatch.setattr(
                app._ocr_worker, "enqueue",
                lambda *a, **kw: enqueued.append((a, kw)),
            )

            with (
                patch("contextpulse_sight.app.capture.capture_all_monitors", return_value=monitors),
                patch("contextpulse_sight.app.capture.find_monitor_at_cursor", return_value=(0, {})),
                patch("mss.mss"),
                patch("contextpulse_sight.app.get_foreground_window_title", return_value="Real Window"),
                patch("contextpulse_sight.app.get_foreground_process_name", return_value="real.exe"),
                patch("contextpulse_sight.app.is_blocked", return_value=False),
            ):
                app._do_auto_capture()

            assert len(enqueued) == 1
            args, kwargs = enqueued[0]
            assert args[2] == "real.exe"
            assert kwargs["window_title"] == "Real Window"
            assert kwargs["monitor_index"] == 0
        finally:
            app._sight_module.stop()
            app._event_bus.close()
            app.activity_db.close()
