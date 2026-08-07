# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Tests for log_rotation — bounds the daemon's log files on disk."""

from __future__ import annotations

import logging
import os
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
