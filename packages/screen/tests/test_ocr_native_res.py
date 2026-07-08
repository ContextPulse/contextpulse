"""Regression: the auto-capture loop must OCR the NATIVE-resolution frame,
not the downscaled buffer image.

Bug (latent since 2026-04, fatal on 4K monitors): app._do_auto_capture unpacked
`(idx, downscaled_img, native_img)` from capture_all_monitors(keep_native=True)
but enqueued `native_img=img` (the DOWNSCALED image) for OCR. On a 3840x2160
monitor the 1280x720 downscale shrinks text below RapidOCR's detection floor
(~79 chars vs ~8500 on native), so every frame classified as "image", `text`
was nulled, no ocr_result event fired, and both the Phase-0 probe and the
Phase-1 KG bridge received zero text. See probe_consolidator: 532 active events
-> 0 facts.

This test feeds distinguishable downscaled (1280x720) and native (3840x2160)
images and asserts the image handed to OCR is the native one.
"""

from unittest.mock import MagicMock, patch

from PIL import Image


def _mk(w: int, h: int) -> Image.Image:
    return Image.new("RGB", (w, h), (123, 45, 67))


def test_auto_capture_ocrs_native_not_downscaled(tmp_path):
    buf_dir = tmp_path / "buffer"
    out_dir = tmp_path / "screenshots"
    out_dir.mkdir()

    downscaled = _mk(1280, 720)      # what the buffer stores
    native = _mk(3840, 2160)         # what OCR must run on

    with (
        patch("contextpulse_sight.buffer.BUFFER_DIR", buf_dir),
        patch("contextpulse_sight.config.BUFFER_DIR", buf_dir),
        patch("contextpulse_sight.config.OUTPUT_DIR", out_dir),
        patch("contextpulse_sight.activity.ACTIVITY_DB_PATH", out_dir / "activity.db"),
        patch("contextpulse_sight.app.FILE_LATEST", out_dir / "screen_latest.jpg"),
        patch("contextpulse_sight.app.get_foreground_window_title", return_value="win"),
        patch("contextpulse_sight.app.get_foreground_process_name", return_value="app.exe"),
        patch("contextpulse_sight.app.is_blocked", return_value=False),
        patch("contextpulse_sight.app.should_run_ocr", return_value=True),
        patch(
            "contextpulse_sight.capture.capture_all_monitors",
            return_value=[(0, downscaled, native)],
        ),
    ):
        from contextpulse_sight.app import ContextPulseSightApp

        app = ContextPulseSightApp()
        app._ocr_worker = MagicMock()  # spy on enqueue; no real OCR thread

        assert app._do_auto_capture(force_ocr=True) is True

        app._ocr_worker.enqueue.assert_called_once()
        _, kwargs = app._ocr_worker.enqueue.call_args
        ocr_img = kwargs["native_img"]
        assert ocr_img.size == (3840, 2160), (
            f"OCR must run on the native 3840x2160 image, got {ocr_img.size} "
            "(the downscaled buffer frame - this is the bug)"
        )
        assert ocr_img is native
