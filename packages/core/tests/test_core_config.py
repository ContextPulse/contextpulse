"""Tests for contextpulse_core.config — persistent JSON config with env var fallback."""

import json

import pytest
from contextpulse_core.config import (
    _DEFAULTS,
    get,
    load_config,
    save_config,
)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Redirect config to temp directory for all tests."""
    import contextpulse_core.config as cfg_mod

    test_appdata = tmp_path / "ContextPulse"
    test_config = test_appdata / "config.json"

    monkeypatch.setattr(cfg_mod, "APPDATA_DIR", test_appdata)
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", test_config)

    # Clear any env var overrides
    for env_name in cfg_mod._ENV_MAP.values():
        monkeypatch.delenv(env_name, raising=False)

    yield test_appdata, test_config


class TestLoadConfig:
    def test_returns_defaults_when_no_file(self, isolated_config):
        cfg = load_config()
        assert cfg["auto_interval"] == 5
        assert cfg["storage_mode"] == "smart"
        assert cfg["hotkey_capture"] == "ctrl+shift+s"
        assert isinstance(cfg["blocklist_patterns"], list)
        assert len(cfg["blocklist_patterns"]) > 0  # has default privacy blocklist
        assert "1Password" in cfg["blocklist_patterns"]
        assert cfg["memory_enabled"] is False

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

    def test_every_bool_default_survives_its_env_var(self, isolated_config, monkeypatch):
        """Covers the whole class, not just the one key that was reported."""
        import contextpulse_core.config as cfg_mod

        bool_keys = [
            key for key, default in cfg_mod._DEFAULTS.items()
            if isinstance(default, bool) and key in cfg_mod._ENV_MAP
        ]
        assert bool_keys, "no boolean key is env-mapped; this test would be vacuous"
        for key in bool_keys:
            monkeypatch.setenv(cfg_mod._ENV_MAP[key], "true")
        cfg = load_config()
        for key in bool_keys:
            assert cfg[key] is True, f"{key} did not coerce from its env var"

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


class TestGetHelper:
    def test_returns_value(self, isolated_config):
        assert get("auto_interval") == 5

    def test_returns_default_for_missing(self, isolated_config):
        assert get("nonexistent", "fallback") == "fallback"
