"""Tests for contextpulse_core.config — persistent JSON config with env var fallback."""

import json

import pytest
from contextpulse_core.config import (
    _CLAMPS,
    _DEFAULTS,
    _ENV_MAP,
    clear_config_cache,
    get,
    load_config,
    save_config,
)

# `isolated_config` is registered by packages/core/tests/conftest.py, which
# imports it from contextpulse_core.testing so the screen, voice and touch
# suites can register the same fixture -- a conftest fixture is only visible
# at or below its own directory (measured: requesting a core-conftest fixture
# from packages/screen/tests fails "fixture ... not found").


@pytest.fixture(autouse=True)
def _isolate_every_test(isolated_config):
    """Preserve this module's original autouse isolation.

    Every test here also names `isolated_config` explicitly for the paths it
    needs; this keeps a future test that forgets to from writing into the
    real %APPDATA%/ContextPulse/config.json.
    """
    return isolated_config


class TestLoadConfig:
    def test_returns_defaults_when_no_file(self, isolated_config):
        cfg = load_config()
        assert cfg["auto_interval"] == 5
        assert cfg["storage_mode"] == "smart"
        assert cfg["hotkey_capture"] == "ctrl+shift+s"
        assert isinstance(cfg["blocklist_patterns"], list)
        assert len(cfg["blocklist_patterns"]) > 0  # has default privacy blocklist
        assert "1Password" in cfg["blocklist_patterns"]

    def test_merges_with_defaults(self, isolated_config):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"auto_interval": 10}))

        cfg = load_config()
        assert cfg["auto_interval"] == 10  # overridden
        assert cfg["storage_mode"] == "smart"  # default preserved

    def test_env_var_overrides_config_file(self, isolated_config, monkeypatch):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"auto_interval": 10}))

        monkeypatch.setenv("CONTEXTPULSE_AUTO_INTERVAL", "20")
        cfg = load_config()
        assert cfg["auto_interval"] == 20  # env wins

    def test_env_var_overrides_for_string(self, isolated_config, monkeypatch):
        monkeypatch.setenv("CONTEXTPULSE_STORAGE_MODE", "visual")
        cfg = load_config()
        assert cfg["storage_mode"] == "visual"

    def test_env_var_overrides_for_float(self, isolated_config, monkeypatch):
        monkeypatch.setenv("CONTEXTPULSE_CHANGE_THRESHOLD", "3.5")
        cfg = load_config()
        assert cfg["change_threshold"] == 3.5

    def test_env_var_overrides_for_list(self, isolated_config, monkeypatch):
        monkeypatch.setenv("CONTEXTPULSE_BLOCKLIST", "bank,password")
        cfg = load_config()
        assert cfg["blocklist_patterns"] == ["bank", "password"]

    @pytest.mark.parametrize(
        "raw,expected",
        [("true", True), ("True", True), ("1", True), ("yes", True),
         ("false", False), ("0", False), ("no", False), ("", False)],
    )
    def test_env_var_overrides_for_bool(self, isolated_config, monkeypatch, raw, expected):
        """bool is a subclass of int, so isinstance(default, int) matches a
        boolean default and the int() branch runs first. Before this was
        fixed, CONTEXTPULSE_KNOWLEDGE_ENABLED=true raised
        ValueError: invalid literal for int() with base 10: 'true'
        out of load_config() -- crashing every caller of config.get(),
        including the daemon's clipboard_enabled lookup at startup.
        """
        monkeypatch.setenv("CONTEXTPULSE_KNOWLEDGE_ENABLED", raw)
        cfg = load_config()
        assert cfg["knowledge_enabled"] is expected

    # ── T21: env bool coercion, across EVERY env-mapped bool ────────────
    @staticmethod
    def _bool_keys() -> list[str]:
        return [k for k, v in _DEFAULTS.items() if isinstance(v, bool) and k in _ENV_MAP]

    @pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("yes", True),
                                              ("TRUE", True), ("Yes", True),
                                              ("false", False), ("0", False), ("no", False),
                                              ("", False), ("bogus", False)])
    def test_every_bool_key_coerces_from_its_env_var(self, isolated_config, monkeypatch, raw, expected):
        """T21. Not just the one key that was reported in the bug: the defect
        was in the isinstance chain, so it belonged to every boolean key with
        an env var, and one key passing proves nothing about the others.
        """
        bool_keys = self._bool_keys()
        assert bool_keys, "no boolean key is env-mapped; this test would be vacuous"
        for key in bool_keys:
            monkeypatch.setenv(_ENV_MAP[key], raw)
        cfg = load_config()
        for key in bool_keys:
            assert cfg[key] is expected, f"{key}={raw!r} coerced to {cfg[key]!r}"

    def test_bool_keys_never_take_the_int_branch(self, isolated_config, monkeypatch):
        """The failure mode was a *type*, not just a value: "1" used to yield
        the int 1 rather than True, which is truthy and so looked fine.
        """
        for key in self._bool_keys():
            monkeypatch.setenv(_ENV_MAP[key], "1")
        cfg = load_config()
        for key in self._bool_keys():
            assert isinstance(cfg[key], bool), f"{key} is {type(cfg[key]).__name__}, not bool"

    def test_clipboard_enabled_is_env_mappable(self, isolated_config, monkeypatch):
        monkeypatch.setenv("CONTEXTPULSE_CLIPBOARD_ENABLED", "false")
        cfg = load_config()
        assert cfg["clipboard_enabled"] is False

    def test_clipboard_enabled_defaults_on(self, isolated_config):
        assert load_config()["clipboard_enabled"] is True

    def test_invalid_storage_mode_falls_back_to_smart(self, isolated_config):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"storage_mode": "bogus"}))

        cfg = load_config()
        assert cfg["storage_mode"] == "smart"

    def test_corrupt_json_returns_defaults(self, isolated_config):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text("not json{{{")

        cfg = load_config()
        assert cfg["auto_interval"] == _DEFAULTS["auto_interval"]

    def test_blocklist_file_appends_without_growing_the_defaults(self, isolated_config, tmp_path):
        """load_config() used to append the file's lines onto the list object
        inside _DEFAULTS, so the blocklist grew by one copy of the file on
        every call for the life of the process.
        """
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        listfile = tmp_path / "block.txt"
        listfile.write_text("# a comment\nSecret Project\n\n", encoding="utf-8")
        config_file.write_text(json.dumps({"blocklist_file": str(listfile)}))

        first = load_config()["blocklist_patterns"]
        second = load_config()["blocklist_patterns"]
        assert "Secret Project" in first
        assert first == second, "blocklist grew between two identical loads"
        assert "Secret Project" not in _DEFAULTS["blocklist_patterns"]

    def test_mutating_a_loaded_list_does_not_touch_defaults(self, isolated_config):
        cfg = load_config()
        cfg["blocklist_patterns"].append("mutation probe")
        assert "mutation probe" not in load_config()["blocklist_patterns"]
        assert "mutation probe" not in _DEFAULTS["blocklist_patterns"]


# ── T22: clamps and normalisation ───────────────────────────────────────
class TestClampsAndNormalisation:
    """Port of contextpulse_sight/tests/test_config.py::TestConfigValidation.

    Sight clamped env values only. This module is now the only path a value
    can take, so the same bounds apply to config.json values too.
    """

    @pytest.mark.parametrize(
        "env_name,raw,expected",
        [
            ("CONTEXTPULSE_JPEG_QUALITY", "999", 100),
            ("CONTEXTPULSE_JPEG_QUALITY", "-5", 1),
            ("CONTEXTPULSE_JPEG_QUALITY", "0", 1),
            ("CONTEXTPULSE_AUTO_INTERVAL", "-10", 0),
            ("CONTEXTPULSE_AUTO_INTERVAL_IDLE", "0", 1),
            ("CONTEXTPULSE_AUTO_IDLE_THRESHOLD", "0", 1),
            ("CONTEXTPULSE_BUFFER_MAX_AGE", "-60", 0),
            ("CONTEXTPULSE_CHANGE_THRESHOLD", "-1.0", 0.0),
            ("CONTEXTPULSE_OCR_DIFF_THRESHOLD", "-2.5", 0.0),
            ("CONTEXTPULSE_EVENT_POLL_INTERVAL", "0.01", 0.1),
            ("CONTEXTPULSE_EVENT_MOVEMENT_THRESHOLD", "10", 50),
            ("CONTEXTPULSE_EVENT_IDLE_THRESHOLD", "1", 5),
            ("CONTEXTPULSE_ACTIVITY_MAX_AGE", "-1", 0),
        ],
    )
    def test_env_values_are_clamped(self, isolated_config, monkeypatch, env_name, raw, expected):
        monkeypatch.setenv(env_name, raw)
        key = next(k for k, v in _ENV_MAP.items() if v == env_name)
        assert load_config()[key] == expected

    @pytest.mark.parametrize(
        "key,stored,expected",
        [
            ("jpeg_quality", 500, 100),
            ("jpeg_quality", 0, 1),
            ("auto_interval", -3, 0),
            ("buffer_max_age", -1, 0),
            ("change_threshold", -0.5, 0.0),
            ("event_poll_interval", 0.0, 0.1),
        ],
    )
    def test_json_values_are_clamped_too(self, isolated_config, key, stored, expected):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({key: stored}))
        assert load_config()[key] == expected

    def test_in_range_values_are_untouched(self, isolated_config):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({k: _DEFAULTS[k] for k in _CLAMPS}))
        cfg = load_config()
        for key in _CLAMPS:
            assert cfg[key] == _DEFAULTS[key], f"{key} was clamped away from its own default"

    def test_non_numeric_value_falls_back_to_default_without_raising(self, isolated_config):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"jpeg_quality": "high"}))
        assert load_config()["jpeg_quality"] == _DEFAULTS["jpeg_quality"]

    def test_bad_env_number_falls_back_to_default_without_raising(self, isolated_config, monkeypatch):
        monkeypatch.setenv("CONTEXTPULSE_MAX_WIDTH", "wide")
        assert load_config()["max_width"] == _DEFAULTS["max_width"]

    @pytest.mark.parametrize("raw", ["VISUAL", "Visual", "vIsUaL"])
    def test_storage_mode_is_lowercased_before_validation(self, isolated_config, monkeypatch, raw):
        """Sight lowercased; core validated without lowercasing, so "Visual"
        silently became "smart" -- the opposite of what the user asked for.
        """
        monkeypatch.setenv("CONTEXTPULSE_STORAGE_MODE", raw)
        assert load_config()["storage_mode"] == "visual"

    def test_storage_mode_from_json_is_lowercased(self, isolated_config):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"storage_mode": "BOTH"}))
        assert load_config()["storage_mode"] == "both"

    def test_always_both_apps_is_lowercased(self, isolated_config):
        """ocr_worker compares against app_name.lower(), so an entry typed as
        "MyApp.EXE" in the Settings dialog could never match.
        """
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"always_both_apps": ["MyApp.EXE", " Other.Exe "]}))
        assert load_config()["always_both_apps"] == ["myapp.exe", "other.exe"]

    def test_always_both_apps_from_env_is_lowercased(self, isolated_config, monkeypatch):
        monkeypatch.setenv("CONTEXTPULSE_ALWAYS_BOTH", "ThinkOrSwim.EXE, Tos.exe")
        assert load_config()["always_both_apps"] == ["thinkorswim.exe", "tos.exe"]

    def test_wrong_type_for_list_key_falls_back_to_default(self, isolated_config):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"blocklist_patterns": "1Password"}))
        assert load_config()["blocklist_patterns"] == _DEFAULTS["blocklist_patterns"]


# ── T23: cache, last-good, atomic save ──────────────────────────────────
class _CountingJson:
    """Proxy for the json module that counts loads() calls.

    Patched onto the config module rather than onto `json` itself so the
    counter can only ever see this module's parses.
    """

    def __init__(self):
        self.load_calls = 0

    def loads(self, *args, **kwargs):
        self.load_calls += 1
        return json.loads(*args, **kwargs)

    def dumps(self, *args, **kwargs):
        return json.dumps(*args, **kwargs)


class TestParsedConfigCache:
    def test_unchanged_file_is_not_reparsed(self, isolated_config, monkeypatch):
        import contextpulse_core.config as cfg_mod

        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"auto_interval": 11}))

        counter = _CountingJson()
        monkeypatch.setattr(cfg_mod, "json", counter)

        assert load_config()["auto_interval"] == 11
        assert counter.load_calls == 1
        for _ in range(20):
            assert load_config()["auto_interval"] == 11
        assert counter.load_calls == 1, "cache re-parsed an unchanged file"

    def test_mtime_bump_triggers_a_reread(self, isolated_config, monkeypatch):
        import os

        import contextpulse_core.config as cfg_mod

        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"auto_interval": 11}))

        counter = _CountingJson()
        monkeypatch.setattr(cfg_mod, "json", counter)

        assert load_config()["auto_interval"] == 11
        # Same byte count, different content: proves the cache key is not
        # size alone. os.utime guarantees the mtime moves even if the write
        # lands inside the filesystem's timestamp granularity.
        config_file.write_text(json.dumps({"auto_interval": 22}))
        st = config_file.stat()
        os.utime(config_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

        assert load_config()["auto_interval"] == 22
        assert counter.load_calls == 2

    def test_cache_does_not_leak_across_config_paths(self, isolated_config, tmp_path, monkeypatch):
        import contextpulse_core.config as cfg_mod

        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"auto_interval": 11}))
        assert load_config()["auto_interval"] == 11

        other = tmp_path / "other" / "config.json"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text(json.dumps({"auto_interval": 33}))
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", other)
        assert load_config()["auto_interval"] == 33

    def test_deleted_file_falls_back_to_defaults(self, isolated_config):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"auto_interval": 11}))
        assert load_config()["auto_interval"] == 11
        config_file.unlink()
        assert load_config()["auto_interval"] == _DEFAULTS["auto_interval"]


class TestLastGoodOnParseFailure:
    def test_truncated_file_keeps_the_last_good_values(self, isolated_config):
        """A reader that catches a half-written file must not silently revert
        to defaults -- for blocklist_patterns that means one capture taken
        with an empty blocklist, which is the failure this whole change
        exists to prevent.
        """
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        good = json.dumps({"auto_interval": 11, "blocklist_patterns": ["Bank"]})
        config_file.write_text(good)
        assert load_config()["auto_interval"] == 11

        config_file.write_text(good[: len(good) // 2])  # torn write
        cfg = load_config()
        assert cfg["auto_interval"] == 11, "fell back to defaults instead of last good"
        assert cfg["blocklist_patterns"] == ["Bank"]

    def test_defaults_when_there_was_never_a_good_parse(self, isolated_config):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text("{ broken")
        assert load_config()["auto_interval"] == _DEFAULTS["auto_interval"]

    def test_recovers_when_the_file_becomes_valid_again(self, isolated_config):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"auto_interval": 11}))
        assert load_config()["auto_interval"] == 11
        config_file.write_text("{ broken")
        assert load_config()["auto_interval"] == 11
        config_file.write_text(json.dumps({"auto_interval": 12}))
        assert load_config()["auto_interval"] == 12

    def test_warns_once_per_failure_not_once_per_read(self, isolated_config, caplog):
        import logging

        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"auto_interval": 11}))
        load_config()

        config_file.write_text("{ broken")
        with caplog.at_level(logging.WARNING, logger="contextpulse_core.config"):
            for _ in range(10):
                load_config()
        warnings = [r for r in caplog.records if "unreadable" in r.getMessage()]
        assert len(warnings) == 1, f"{len(warnings)} warnings for one failure"

    def test_last_good_does_not_cross_config_files(self, isolated_config, tmp_path, monkeypatch):
        """Last-good is scoped to one file. A corrupt file must not inherit a
        different file's values just because that one parsed cleanly.
        """
        import contextpulse_core.config as cfg_mod

        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"auto_interval": 11}))
        assert load_config()["auto_interval"] == 11

        other = tmp_path / "other" / "config.json"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text("{ broken")
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", other)
        assert load_config()["auto_interval"] == _DEFAULTS["auto_interval"]
        # ... and again, on the already-warned path
        assert load_config()["auto_interval"] == _DEFAULTS["auto_interval"]

    def test_non_object_json_is_treated_as_a_failure(self, isolated_config):
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text("[1, 2, 3]")
        assert load_config()["auto_interval"] == _DEFAULTS["auto_interval"]


class TestSaveConfig:
    def test_creates_directory_and_file(self, isolated_config):
        appdata, config_file = isolated_config
        save_config({"auto_interval": 15, "storage_mode": "smart"})
        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert data["auto_interval"] == 15
        # "smart" is default, should NOT be in saved file
        assert "storage_mode" not in data

    def test_only_saves_non_default_values(self, isolated_config):
        _, config_file = isolated_config
        save_config(dict(_DEFAULTS))  # all defaults
        data = json.loads(config_file.read_text())
        assert data == {}  # nothing differs from defaults

    def test_preserves_unknown_keys(self, isolated_config):
        _, config_file = isolated_config
        save_config({"custom_setting": "foo", "auto_interval": 5})
        data = json.loads(config_file.read_text())
        assert data["custom_setting"] == "foo"
        assert "auto_interval" not in data  # default value, not saved

    def test_leaves_no_tmp_file_behind(self, isolated_config):
        appdata, config_file = isolated_config
        save_config({"auto_interval": 15})
        leftovers = [p.name for p in appdata.iterdir() if p.name != "config.json"]
        assert leftovers == [], f"save left {leftovers} behind"

    def test_leaves_no_tmp_file_behind_when_the_replace_fails(self, isolated_config, monkeypatch):
        """The tmp file must not survive a failed save either -- otherwise a
        stale config.json.tmp sits next to the real file forever.
        """
        import contextpulse_core.config as cfg_mod

        appdata, config_file = isolated_config

        def boom(src, dst):
            raise OSError("replace failed")

        monkeypatch.setattr(cfg_mod.os, "replace", boom)
        save_config({"auto_interval": 15})  # must not raise
        assert not config_file.exists()
        assert list(appdata.iterdir()) == []

    def test_a_reader_never_sees_a_partial_file(self, isolated_config):
        """os.replace is atomic, so the only two states a reader can observe
        are the old content and the new one.
        """
        appdata, config_file = isolated_config
        appdata.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps({"auto_interval": 11}))
        save_config({"auto_interval": 12, "blocklist_patterns": ["Bank"]})
        data = json.loads(config_file.read_text())
        assert data["auto_interval"] == 12
        assert load_config()["blocklist_patterns"] == ["Bank"]

    def test_save_invalidates_the_cache(self, isolated_config):
        """Without this, a save whose file lands with the same size inside the
        clock's granularity could be masked by the previous parse.
        """
        assert load_config()["auto_interval"] == 5
        save_config({"auto_interval": 7})
        assert load_config()["auto_interval"] == 7

    def test_clear_config_cache_is_idempotent(self, isolated_config):
        clear_config_cache()
        clear_config_cache()
        assert load_config()["auto_interval"] == _DEFAULTS["auto_interval"]


# ── T24: the defaults table itself ──────────────────────────────────────
class TestDefaultsTable:
    NEW_KEYS = (
        "auto_interval_idle",
        "auto_idle_threshold",
        "ocr_diff_threshold",
        "touch_burst_timeout",
        "touch_correction_window",
        "touch_min_burst_chars",
        "touch_mouse_debounce",
    )
    REMOVED_KEYS = ("memory_enabled", "memory_tier", "output_dir")

    @pytest.mark.parametrize("key,expected", [
        ("auto_interval_idle", 30),
        ("auto_idle_threshold", 60),
        ("ocr_diff_threshold", 5.0),
        ("touch_burst_timeout", 1.5),
        ("touch_correction_window", 15.0),
        ("touch_min_burst_chars", 3),
        ("touch_mouse_debounce", 0.1),
    ])
    def test_new_keys_exist_with_the_sight_and_touch_values(self, key, expected):
        assert _DEFAULTS[key] == expected

    @pytest.mark.parametrize("key", REMOVED_KEYS)
    def test_removed_keys_are_gone(self, key):
        assert key not in _DEFAULTS, f"{key} still declared"
        assert key not in _ENV_MAP, f"{key} still env-mapped"

    def test_jpeg_quality_matches_the_running_daemon(self):
        """75 was the core default; 90 is what the capture pipeline has
        actually been using. Unifying on 90 keeps the cutover a plumbing
        change (contextpulse_sight.config.JPEG_QUALITY).
        """
        assert _DEFAULTS["jpeg_quality"] == 90

    def test_voice_whisper_model_stays_small(self):
        assert _DEFAULTS["voice_whisper_model"] == "small"

    def test_default_blocklist_has_its_fourteen_patterns(self):
        assert len(_DEFAULTS["blocklist_patterns"]) == 14

    def test_key_count(self):
        """29 on this branch - 3 removed + 7 added."""
        assert len(_DEFAULTS) == 33

    def test_output_dir_is_a_module_constant_not_a_config_key(self):
        from pathlib import Path

        from contextpulse_core.config import ACTIVITY_DB_PATH, OUTPUT_DIR

        assert isinstance(OUTPUT_DIR, Path)
        assert isinstance(ACTIVITY_DB_PATH, Path)
        assert ACTIVITY_DB_PATH.parent == OUTPUT_DIR

    def test_every_env_map_key_is_a_defaults_key(self):
        assert set(_ENV_MAP) <= set(_DEFAULTS), sorted(set(_ENV_MAP) - set(_DEFAULTS))

    def test_every_env_var_is_namespaced(self):
        for key, env_name in _ENV_MAP.items():
            assert env_name.startswith("CONTEXTPULSE_"), f"{key} -> {env_name}"

    def test_every_clamped_key_is_a_defaults_key(self):
        assert set(_CLAMPS) <= set(_DEFAULTS), sorted(set(_CLAMPS) - set(_DEFAULTS))

    def test_the_four_touch_env_vars_keep_their_existing_names(self):
        """These names already existed in contextpulse_touch.config; moving the
        declaration must not rename a variable a user may have set.
        """
        assert _ENV_MAP["touch_burst_timeout"] == "CONTEXTPULSE_TOUCH_BURST_TIMEOUT"
        assert _ENV_MAP["touch_correction_window"] == "CONTEXTPULSE_TOUCH_CORRECTION_WINDOW"
        assert _ENV_MAP["touch_min_burst_chars"] == "CONTEXTPULSE_TOUCH_MIN_BURST_CHARS"
        assert _ENV_MAP["touch_mouse_debounce"] == "CONTEXTPULSE_TOUCH_MOUSE_DEBOUNCE"


class TestGetHelper:
    def test_returns_value(self, isolated_config):
        assert get("auto_interval") == 5

    def test_returns_default_for_missing(self, isolated_config):
        assert get("nonexistent", "fallback") == "fallback"

    def test_reflects_a_saved_change_without_a_reload(self, isolated_config):
        assert get("jpeg_quality") == 90
        save_config({"jpeg_quality": 40})
        assert get("jpeg_quality") == 40
