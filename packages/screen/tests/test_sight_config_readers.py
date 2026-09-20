# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""The remaining sight readers take their values from the saved config.

capture.py, activity.py and events.py have no dedicated Settings control
between them, which is exactly why their tunables were never suspected: the
audit's dead-controls ledger found them as system-B duplicates of keys that
core config already declared (row 30). Nothing asserted where the values came
from, so nothing noticed that config.json could not reach them.

Each test sets the value through the real config file and exercises the real
function, patching nothing on the config path.
"""

from io import BytesIO

from contextpulse_core.config import save_config
from PIL import Image


def _make_image(width, height, color=(128, 128, 128)):
    return Image.new("RGB", (width, height), color)


class TestCaptureReadsConfig:
    def test_downscale_bounds_come_from_config(self, isolated_config):
        from contextpulse_sight import capture

        save_config({"max_width": 320, "max_height": 180})
        out = capture._downscale(_make_image(1920, 1080))
        assert out.width <= 320 and out.height <= 180

    def test_a_larger_bound_leaves_the_image_alone(self, isolated_config):
        """Positive control: the bound is read, not a hardcoded shrink."""
        from contextpulse_sight import capture

        save_config({"max_width": 4000, "max_height": 4000})
        out = capture._downscale(_make_image(1920, 1080))
        assert (out.width, out.height) == (1920, 1080)

    def test_save_image_honours_the_saved_jpeg_quality(self, tmp_path, isolated_config):
        import time

        from contextpulse_sight import capture

        img = _make_image(200, 200)
        for x in range(200):  # noise, so quality actually changes the size
            for y in range(0, 200, 7):
                img.putpixel((x, y), (x % 256, y % 256, (x * y) % 256))

        save_config({"jpeg_quality": 10})
        low = tmp_path / "low.jpg"
        capture.save_image(img, low, fmt="JPEG")

        time.sleep(0.01)
        save_config({"jpeg_quality": 95})
        high = tmp_path / "high.jpg"
        capture.save_image(img, high, fmt="JPEG")

        assert low.stat().st_size < high.stat().st_size

    def test_capture_to_bytes_honours_the_saved_jpeg_quality(self, isolated_config):
        import time

        from contextpulse_sight import capture

        img = _make_image(200, 200)
        for x in range(200):
            for y in range(0, 200, 7):
                img.putpixel((x, y), (x % 256, y % 256, (x * y) % 256))

        save_config({"jpeg_quality": 10})
        low = capture.capture_to_bytes(img, fmt="JPEG")
        time.sleep(0.01)
        save_config({"jpeg_quality": 95})
        high = capture.capture_to_bytes(img, fmt="JPEG")

        assert len(low) < len(high)
        assert Image.open(BytesIO(high)).size == (200, 200)


class TestActivityReadsConfig:
    def test_prune_falls_back_to_the_saved_activity_max_age(self, tmp_path, isolated_config):
        import time

        from contextpulse_sight.activity import ActivityDB

        db = ActivityDB(db_path=tmp_path / "test.db")
        try:
            db.record(time.time() - 3600, "an hour ago", "old.exe")
            save_config({"activity_max_age": 86400})
            db.prune()
            assert db.count() == 1, "a 1h-old row was pruned at max_age=86400"

            time.sleep(0.01)
            save_config({"activity_max_age": 60})
            db.prune()
            assert db.count() == 0, (
                "the new activity_max_age did not reach prune() -- the value "
                "is bound at import, not read per call"
            )
        finally:
            db.close()

    def test_an_explicit_argument_still_wins(self, tmp_path, isolated_config):
        import time

        from contextpulse_sight.activity import ActivityDB

        db = ActivityDB(db_path=tmp_path / "test.db")
        try:
            db.record(time.time() - 3600, "an hour ago", "old.exe")
            save_config({"activity_max_age": 86400})
            db.prune(max_age_seconds=60)
            assert db.count() == 0
        finally:
            db.close()


class TestEventDetectorReadsConfig:
    def test_thresholds_come_from_the_saved_config(self, isolated_config):
        from contextpulse_sight.events import EventDetector

        save_config({
            "event_poll_interval": 2.5,
            "event_idle_threshold": 99,
            "event_movement_threshold": 777,
        })
        detector = EventDetector(get_cursor_pos=lambda: (0, 0), find_monitor_index=lambda x, y: 0)
        assert detector._poll_interval == 2.5
        assert detector._idle_threshold == 99
        assert detector._movement_threshold == 777

    def test_defaults_apply_with_no_config_file(self, isolated_config):
        from contextpulse_sight.events import EventDetector

        detector = EventDetector(get_cursor_pos=lambda: (0, 0), find_monitor_index=lambda x, y: 0)
        assert detector._poll_interval == 0.5
        assert detector._idle_threshold == 30
        assert detector._movement_threshold == 200

    def test_a_movement_below_the_saved_threshold_does_not_fire(self, isolated_config):
        """The value is used, not merely stored on the instance."""
        from contextpulse_sight.events import EventDetector

        save_config({"event_movement_threshold": 5000})
        detector = EventDetector(
            get_cursor_pos=lambda: (4000, 0), find_monitor_index=lambda cx, cy: 1
        )
        detector._check_cursor_activity()
        assert not detector.has_pending_event()

        detector._movement_threshold = 100
        detector._last_cursor = (0, 0)
        detector._check_cursor_activity()
        assert detector.has_pending_event()
