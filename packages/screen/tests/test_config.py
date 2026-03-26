"""Tests for config.py — validation of env var parsing and clamping."""

import os
from unittest.mock import patch


class TestConfigDefaults:
    """Test that default config values are correct."""

    def test_default_values(self):
        from contextpulse_sight.config import (
            AUTO_INTERVAL, BUFFER_MAX_AGE, CHANGE_THRESHOLD,
            JPEG_QUALITY, MAX_HEIGHT, MAX_WIDTH,
        )
        assert MAX_WIDTH == 1280
        assert MAX_HEIGHT == 720
        assert JPEG_QUALITY == 75
        assert AUTO_INTERVAL == 5
        assert BUFFER_MAX_AGE == 1800
        assert CHANGE_THRESHOLD == 0.5

    def test_output_dir_is_path(self):
        from contextpulse_sight.config import OUTPUT_DIR
        from pathlib import Path
        assert isinstance(OUTPUT_DIR, Path)

    def test_file_paths_are_under_output_dir(self):
        from contextpulse_sight.config import (
            FILE_ALL, FILE_LATEST, FILE_REGION, OUTPUT_DIR,
        )
        assert FILE_LATEST.parent == OUTPUT_DIR
        assert FILE_ALL.parent == OUTPUT_DIR
        assert FILE_REGION.parent == OUTPUT_DIR

    def test_buffer_dir_is_under_output_dir(self):
        from contextpulse_sight.config import BUFFER_DIR, OUTPUT_DIR
        assert BUFFER_DIR.parent == OUTPUT_DIR
        assert BUFFER_DIR.name == "buffer"


class TestConfigValidation:
    """Test that config values are clamped to valid ranges."""

    def test_jpeg_quality_clamped_high(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_JPEG_QUALITY": "999"}):
            # Re-import to pick up new env
            import importlib
            import contextpulse_sight.config as cfg
            importlib.reload(cfg)
            assert cfg.JPEG_QUALITY == 100

    def test_jpeg_quality_clamped_low(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_JPEG_QUALITY": "-5"}):
            import importlib
            import contextpulse_sight.config as cfg
            importlib.reload(cfg)
            assert cfg.JPEG_QUALITY == 1

    def test_auto_interval_non_negative(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_AUTO_INTERVAL": "-10"}):
            import importlib
            import contextpulse_sight.config as cfg
            importlib.reload(cfg)
            assert cfg.AUTO_INTERVAL == 0

    def test_buffer_max_age_non_negative(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_BUFFER_MAX_AGE": "-60"}):
            import importlib
            import contextpulse_sight.config as cfg
            importlib.reload(cfg)
            assert cfg.BUFFER_MAX_AGE == 0

    def test_change_threshold_non_negative(self):
        with patch.dict(os.environ, {"CONTEXTPULSE_CHANGE_THRESHOLD": "-1.0"}):
            import importlib
            import contextpulse_sight.config as cfg
            importlib.reload(cfg)
            assert cfg.CHANGE_THRESHOLD == 0.0
