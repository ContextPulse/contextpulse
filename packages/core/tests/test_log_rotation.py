# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for log_rotation — bounds the daemon's log files on disk."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

import pytest
from contextpulse_core import log_rotation


@pytest.fixture(autouse=True)
def clean_log_env() -> Iterator[None]:
    """Isolate every test from ambient CONTEXTPULSE_LOG_* overrides."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CONTEXTPULSE_LOG_MAX_BYTES", None)
        os.environ.pop("CONTEXTPULSE_LOG_BACKUP_COUNT", None)
        yield


class TestEnvInt:
    def test_missing_var_returns_default(self):
        assert log_rotation.env_int("CONTEXTPULSE_NOT_SET_ANYWHERE", 7) == 7

    def test_valid_value_is_used(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_LOG_MAX_BYTES": "512"}):
            assert log_rotation.env_int("CONTEXTPULSE_LOG_MAX_BYTES", 7) == 512

    def test_unparseable_value_falls_back_to_default(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_LOG_MAX_BYTES": "ten-megs"}):
            assert log_rotation.env_int("CONTEXTPULSE_LOG_MAX_BYTES", 7) == 7

    def test_below_minimum_falls_back_to_default(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_LOG_MAX_BYTES": "0"}):
            assert log_rotation.env_int("CONTEXTPULSE_LOG_MAX_BYTES", 7, minimum=1) == 7


class TestGetters:
    def test_max_bytes_defaults_to_10_mib(self):
        assert log_rotation.get_max_bytes() == 10 * 1024 * 1024

    def test_backup_count_defaults_to_3(self):
        assert log_rotation.get_backup_count() == 3

    def test_backup_count_of_zero_is_honoured(self):
        # 0 is meaningful here ("keep nothing"), unlike max_bytes where it
        # would mean "roll on every record".
        with patch.dict(os.environ, {"CONTEXTPULSE_LOG_BACKUP_COUNT": "0"}):
            assert log_rotation.get_backup_count() == 0


class TestRotatingFileHandler:
    def test_returns_a_rotating_handler(self, tmp_path: Path):
        handler = log_rotation.rotating_file_handler(tmp_path / "x.log")
        try:
            assert isinstance(handler, RotatingFileHandler)
        finally:
            handler.close()

    def test_carries_configured_limits(self, tmp_path: Path):
        handler = log_rotation.rotating_file_handler(tmp_path / "x.log", max_bytes=99, backup_count=2)
        try:
            assert handler.maxBytes == 99
            assert handler.backupCount == 2
        finally:
            handler.close()

    def test_does_not_create_the_file_until_first_record(self, tmp_path: Path):
        path = tmp_path / "x.log"
        handler = log_rotation.rotating_file_handler(path)
        try:
            assert not path.exists()
        finally:
            handler.close()

    def test_writing_past_max_bytes_creates_a_backup(self, tmp_path: Path):
        path = tmp_path / "x.log"
        handler = log_rotation.rotating_file_handler(path, max_bytes=200, backup_count=2)
        logger = logging.getLogger("test.rotation.handler")
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            for i in range(50):
                logger.info("padding line %03d %s", i, "y" * 40)
        finally:
            logger.removeHandler(handler)
            handler.close()

        assert path.with_name("x.log.1").exists()
        # The whole point: the active file stays bounded.
        assert path.stat().st_size <= 400


class TestRotateIfOversized:
    def test_missing_file_is_a_noop(self, tmp_path: Path):
        assert log_rotation.rotate_if_oversized(tmp_path / "nope.log", max_bytes=10) is False

    def test_small_file_is_left_alone(self, tmp_path: Path):
        path = tmp_path / "crash.log"
        path.write_text("tiny", encoding="utf-8")
        assert log_rotation.rotate_if_oversized(path, max_bytes=1024) is False
        assert path.read_text(encoding="utf-8") == "tiny"

    def test_oversized_file_moves_to_generation_1(self, tmp_path: Path):
        path = tmp_path / "crash.log"
        path.write_text("x" * 100, encoding="utf-8")
        assert log_rotation.rotate_if_oversized(path, max_bytes=10, backup_count=2) is True
        assert not path.exists()
        assert path.with_name("crash.log.1").read_text(encoding="utf-8") == "x" * 100

    def test_existing_generations_shift_down(self, tmp_path: Path):
        path = tmp_path / "crash.log"
        path.write_text("current", encoding="utf-8")
        path.with_name("crash.log.1").write_text("older", encoding="utf-8")
        log_rotation.rotate_if_oversized(path, max_bytes=1, backup_count=3)
        assert path.with_name("crash.log.1").read_text(encoding="utf-8") == "current"
        assert path.with_name("crash.log.2").read_text(encoding="utf-8") == "older"

    def test_generations_past_backup_count_are_dropped(self, tmp_path: Path):
        path = tmp_path / "crash.log"
        path.write_text("current", encoding="utf-8")
        path.with_name("crash.log.1").write_text("gen1", encoding="utf-8")
        path.with_name("crash.log.2").write_text("gen2-should-be-dropped", encoding="utf-8")
        log_rotation.rotate_if_oversized(path, max_bytes=1, backup_count=2)
        assert path.with_name("crash.log.1").read_text(encoding="utf-8") == "current"
        assert path.with_name("crash.log.2").read_text(encoding="utf-8") == "gen1"
        assert not path.with_name("crash.log.3").exists()

    def test_backup_count_zero_deletes_without_keeping_a_copy(self, tmp_path: Path):
        path = tmp_path / "crash.log"
        path.write_text("x" * 100, encoding="utf-8")
        assert log_rotation.rotate_if_oversized(path, max_bytes=10, backup_count=0) is True
        assert not path.exists()
        assert not path.with_name("crash.log.1").exists()

    def test_repeated_appends_stay_bounded(self, tmp_path: Path):
        """The crash-reporter loop: rotate, append, rotate, append..."""
        path = tmp_path / "crash.log"
        for _ in range(20):
            log_rotation.rotate_if_oversized(path, max_bytes=100, backup_count=1)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("z" * 60)
        total = sum(p.stat().st_size for p in tmp_path.iterdir())
        # Active file + 1 backup, each rolled at ~100 bytes.
        assert total < 400

    def test_os_error_is_swallowed_not_raised(self, tmp_path: Path):
        """A crash reporter must never be the thing that crashes."""
        path = tmp_path / "crash.log"
        path.write_text("x" * 100, encoding="utf-8")
        with patch.object(Path, "replace", side_effect=OSError("locked")):
            assert log_rotation.rotate_if_oversized(path, max_bytes=10) is False
        assert path.exists()


# ---------------------------------------------------------------------------
# RepeatDedupeFilter / get_repeat_dedupe_filter
#
# Regression coverage for cp-daemon-session0-blind-capture: a single stuck
# error looped every ~30s for 4 days and grew daemon_stderr.log to 20.5MB,
# because that file is only rotated at process RESTART and the daemon never
# crashed. These tests drive the filter directly against real LogRecords
# (via a real logger + a capturing handler) rather than hand-building
# LogRecord objects, so record.getMessage()'s %-formatting is exercised for
# real.
# ---------------------------------------------------------------------------


def _make_logger(name: str, dedupe_filter: log_rotation.RepeatDedupeFilter) -> tuple[logging.Logger, list[str]]:
    logger = logging.getLogger(name)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.filters.clear()

    captured: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    logger.addHandler(_ListHandler())
    logger.addFilter(dedupe_filter)
    return logger, captured


class TestRepeatDedupeFilter:
    def test_first_occurrence_always_passes(self):
        f = log_rotation.RepeatDedupeFilter(threshold=3, repeat_every=10)
        logger, captured = _make_logger("test.dedupe.first", f)
        logger.error("boom")
        assert captured == ["boom"]

    def test_occurrences_up_to_threshold_all_pass_unmodified(self):
        f = log_rotation.RepeatDedupeFilter(threshold=3, repeat_every=10)
        logger, captured = _make_logger("test.dedupe.threshold", f)
        for _ in range(3):
            logger.error("same message")
        assert captured == ["same message"] * 3

    def test_occurrence_past_threshold_is_suppressed(self):
        f = log_rotation.RepeatDedupeFilter(threshold=3, repeat_every=100)
        logger, captured = _make_logger("test.dedupe.suppress", f)
        for _ in range(4):
            logger.error("same message")
        # 3 pass through as-is, the 4th becomes the "suppressing" notice --
        # nothing is silently dropped without SOME trace in the log.
        assert len(captured) == 4
        assert captured[:3] == ["same message"] * 3
        assert "suppressing" in captured[3]

    def test_a_stuck_loop_is_bounded_not_silenced(self):
        """The actual incident, replayed: 300 identical records (a stand-in
        for '10,524 over 4 days') must produce far fewer than 300 log
        lines, AND at least one must still be getting through -- this is
        rate limiting, not a black hole."""
        f = log_rotation.RepeatDedupeFilter(threshold=5, repeat_every=20)
        logger, captured = _make_logger("test.dedupe.stuck_loop", f)
        for _ in range(300):
            logger.exception("Auto-capture failed (%d consecutive)", 1)
        assert 0 < len(captured) < 30, (
            f"expected heavy suppression of a 300x repeat, got {len(captured)} lines through"
        )
        # The very last (300th) occurrence must be one of the periodic
        # reminders, not silence -- prove the loop never goes fully dark.
        assert captured[-1] != captured[0]

    def test_different_message_resets_the_count(self):
        f = log_rotation.RepeatDedupeFilter(threshold=1, repeat_every=10)
        logger, captured = _make_logger("test.dedupe.reset", f)
        logger.error("A")  # 1st: passes clean
        logger.error("A")  # 2nd: past threshold -> announce, still passes
        logger.error("A")  # 3rd: genuinely suppressed (not a multiple of repeat_every)
        logger.error("B")  # different signature -- resets, always passes
        assert len(captured) == 3
        assert captured[0] == "A"
        assert "repeating identically" in captured[1]
        assert captured[2] == "B"

    def test_different_level_is_a_different_signature(self):
        f = log_rotation.RepeatDedupeFilter(threshold=1, repeat_every=10)
        logger, captured = _make_logger("test.dedupe.level", f)
        logger.setLevel(logging.DEBUG)
        logger.warning("same text")
        logger.error("same text")  # different level -> not a repeat of the WARNING
        assert captured == ["same text", "same text"]

    def test_a_gap_longer_than_the_window_resets_the_count(self):
        f = log_rotation.RepeatDedupeFilter(threshold=1, repeat_every=100, window_seconds=0.05)
        logger, captured = _make_logger("test.dedupe.window", f)
        logger.error("same message")  # 1st: passes clean
        logger.error("same message")  # 2nd: past threshold -> announce, passes
        logger.error("same message")  # 3rd: genuinely suppressed (same window)
        assert len(captured) == 2
        time.sleep(0.1)
        logger.error("same message")  # window elapsed -> treated as fresh, passes
        assert len(captured) == 3
        assert captured[2] == "same message"

    def test_percent_formatted_args_are_deduped_by_final_text(self):
        """Two records with the same literal msg template but the SAME
        rendered text (e.g. a fixed arg) are one signature; this also
        proves record.args=() doesn't corrupt the summary line's own %
        characters."""
        f = log_rotation.RepeatDedupeFilter(threshold=1, repeat_every=2)
        logger, captured = _make_logger("test.dedupe.percent", f)
        for _ in range(4):
            logger.error("rate is %d%% (%s)", 50, "steady")
        # threshold=1 -> 1 passes clean, then every 2nd is a repeat-count
        # notice; none of this may raise even though the rendered text
        # itself contains a literal '%'.
        assert captured[0] == "rate is 50% (steady)"
        assert all("%" in line for line in captured)

    def test_formatting_failure_never_hides_the_record(self):
        f = log_rotation.RepeatDedupeFilter(threshold=1, repeat_every=10)
        logger, captured = _make_logger("test.dedupe.badfmt", f)
        logger.error("needs an arg: %s")  # missing arg -> getMessage() raises
        assert len(captured) == 1

    def test_thread_safety_under_concurrent_logging(self):
        """Best-effort counting under concurrency must not raise or drop
        the filter into an inconsistent state that blocks all output."""
        f = log_rotation.RepeatDedupeFilter(threshold=5, repeat_every=10)
        logger, captured = _make_logger("test.dedupe.threads", f)

        def _hammer():
            for _ in range(100):
                logger.error("concurrent message")

        threads = [threading.Thread(target=_hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not any(t.is_alive() for t in threads)
        assert len(captured) > 0  # some output got through; nothing deadlocked


class TestGetRepeatDedupeFilter:
    def test_defaults_match_module_constants(self):
        with patch.dict(os.environ, {}, clear=False):
            for key in (
                "CONTEXTPULSE_LOG_REPEAT_THRESHOLD",
                "CONTEXTPULSE_LOG_REPEAT_EVERY",
                "CONTEXTPULSE_LOG_REPEAT_WINDOW_SEC",
            ):
                os.environ.pop(key, None)
            f = log_rotation.get_repeat_dedupe_filter()
        assert f.threshold == log_rotation.DEFAULT_REPEAT_THRESHOLD
        assert f.repeat_every == log_rotation.DEFAULT_REPEAT_EVERY
        assert f.window_seconds == log_rotation.DEFAULT_REPEAT_WINDOW_SECONDS

    def test_env_overrides_are_honoured(self):
        with patch.dict(
            os.environ,
            {
                "CONTEXTPULSE_LOG_REPEAT_THRESHOLD": "2",
                "CONTEXTPULSE_LOG_REPEAT_EVERY": "7",
                "CONTEXTPULSE_LOG_REPEAT_WINDOW_SEC": "12.5",
            },
        ):
            f = log_rotation.get_repeat_dedupe_filter()
        assert f.threshold == 2
        assert f.repeat_every == 7
        assert f.window_seconds == 12.5

    def test_garbage_window_env_degrades_to_default(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_LOG_REPEAT_WINDOW_SEC": "not-a-number"}):
            f = log_rotation.get_repeat_dedupe_filter()
        assert f.window_seconds == log_rotation.DEFAULT_REPEAT_WINDOW_SECONDS

    def test_explicit_args_override_env(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_LOG_REPEAT_THRESHOLD": "99"}):
            f = log_rotation.get_repeat_dedupe_filter(threshold=1)
        assert f.threshold == 1
