# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Persistent configuration for all ContextPulse packages.

Reads from %APPDATA%/ContextPulse/config.json with env var overrides.
Env vars always win (backward compat with Sight's CONTEXTPULSE_* vars).
Missing keys in config.json are filled from _DEFAULTS on load.
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load optional workspace-level .env from CONTEXTPULSE_DOTENV env var, then local overrides.
# This avoids hardcoding any specific user's directory structure.
_workspace_dotenv = os.environ.get("CONTEXTPULSE_DOTENV", "")
if _workspace_dotenv:
    load_dotenv(_workspace_dotenv, override=True)
load_dotenv(override=True)  # local .env overrides everything


def env(key: str, default: str) -> str:
    """Read an environment variable with a default."""
    return os.environ.get(key, default)


# ── Platform-wide paths ──────────────────────────────────────────────
if sys.platform == "darwin":
    APPDATA_DIR = Path.home() / "Library" / "Application Support" / "ContextPulse"
elif sys.platform == "win32":
    APPDATA_DIR = Path(os.environ.get("APPDATA", "")) / "ContextPulse"
else:  # Linux
    APPDATA_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "ContextPulse"
CONFIG_FILE = APPDATA_DIR / "config.json"
CONTEXTPULSE_HOME = Path(env("CONTEXTPULSE_HOME", str(Path.home() / ".contextpulse")))

# ── Data paths (shared across all packages) ──────────────────────────
_default_output = str(Path.home() / "Pictures" / "ContextPulse") if sys.platform == "darwin" else str(Path.home() / "screenshots")
OUTPUT_DIR = Path(env("CONTEXTPULSE_OUTPUT_DIR", _default_output))
ACTIVITY_DB_PATH = OUTPUT_DIR / env("CONTEXTPULSE_ACTIVITY_DB", "activity.db")

# ── Defaults ─────────────────────────────────────────────────────────
# These define every configurable setting and its default value.
# config.json stores user overrides; env vars override everything.
_DEFAULTS: dict = {
    # Sight — capture settings
    "output_dir": str(Path.home() / "screenshots"),
    "auto_interval": 5,           # seconds (0 = disabled)
    "buffer_max_age": 1800,       # seconds (30 min)
    "change_threshold": 0.5,      # % pixel difference for dedup
    "max_width": 1280,
    "max_height": 720,
    "jpeg_quality": 75,
    "storage_mode": "smart",      # smart | visual | both | text

    # Sight — hotkeys
    "hotkey_capture": "ctrl+shift+s",
    "hotkey_all_monitors": "ctrl+shift+a",
    "hotkey_region": "ctrl+shift+z",
    "hotkey_pause": "ctrl+shift+p",

    # Sight — privacy (default blocklist catches common sensitive apps)
    "blocklist_patterns": [
        "1Password", "Bitwarden", "KeePass", "LastPass", "Dashlane",
        "Password Manager",
        "Sign in", "Log in", "Enter your password",
        "Windows Security",
        "Authenticator", "Two-Factor", "2FA", "Verification code",
    ],
    "blocklist_file": "",         # path to blocklist file
    "always_both_apps": ["thinkorswim.exe"],

    # Privacy — OCR redaction (masks API keys, passwords, etc. before storage)
    "redact_ocr_text": True,

    # Activity tracking
    "activity_max_age": 86400,    # seconds (24h)

    # Event-driven capture
    "event_poll_interval": 0.5,   # seconds
    "event_movement_threshold": 200,  # pixels
    "event_idle_threshold": 30,   # seconds

    # Memory — feature flags (future)
    "memory_enabled": False,
    "memory_tier": "",            # "" | "starter" | "pro"

    # Voice — dictation settings
    "voice_hotkey": "ctrl+space",
    "voice_fix_hotkey": "ctrl+shift+space",
    "voice_whisper_model": "base",     # base | small | medium | large
    "voice_always_use_llm": False,
    "voice_anthropic_api_key": "",
}

# ── Env var mapping ──────────────────────────────────────────────────
# Maps config keys → CONTEXTPULSE_* env var names (backward compat).
_ENV_MAP: dict[str, str] = {
    "output_dir": "CONTEXTPULSE_OUTPUT_DIR",
    "auto_interval": "CONTEXTPULSE_AUTO_INTERVAL",
    "buffer_max_age": "CONTEXTPULSE_BUFFER_MAX_AGE",
    "change_threshold": "CONTEXTPULSE_CHANGE_THRESHOLD",
    "max_width": "CONTEXTPULSE_MAX_WIDTH",
    "max_height": "CONTEXTPULSE_MAX_HEIGHT",
    "jpeg_quality": "CONTEXTPULSE_JPEG_QUALITY",
    "storage_mode": "CONTEXTPULSE_STORAGE_MODE",
    "blocklist_file": "CONTEXTPULSE_BLOCKLIST_FILE",
    "always_both_apps": "CONTEXTPULSE_ALWAYS_BOTH",
    "blocklist_patterns": "CONTEXTPULSE_BLOCKLIST",
    "activity_max_age": "CONTEXTPULSE_ACTIVITY_MAX_AGE",
    "event_poll_interval": "CONTEXTPULSE_EVENT_POLL_INTERVAL",
    "event_movement_threshold": "CONTEXTPULSE_EVENT_MOVEMENT_THRESHOLD",
    "event_idle_threshold": "CONTEXTPULSE_EVENT_IDLE_THRESHOLD",
    "voice_hotkey": "CONTEXTPULSE_VOICE_HOTKEY",
    "voice_fix_hotkey": "CONTEXTPULSE_VOICE_FIX_HOTKEY",
    "voice_whisper_model": "CONTEXTPULSE_VOICE_MODEL",
    "voice_always_use_llm": "CONTEXTPULSE_VOICE_ALWAYS_LLM",
}


def load_config() -> dict:
    """Load config.json merged with defaults, then apply env var overrides."""
    config = dict(_DEFAULTS)

    # Layer 1: config.json overrides defaults
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            config.update(data)
        except Exception:
            logger.debug("Failed to read config.json", exc_info=True)

    # Layer 2: env vars override config.json
    for key, env_name in _ENV_MAP.items():
        val = os.environ.get(env_name)
        if val is None:
            continue
        default = _DEFAULTS[key]
        if isinstance(default, int):
            config[key] = int(val)
        elif isinstance(default, float):
            config[key] = float(val)
        elif isinstance(default, bool):
            config[key] = val.lower() in ("1", "true", "yes")
        elif isinstance(default, list):
            config[key] = [p.strip() for p in val.split(",") if p.strip()]
        else:
            config[key] = val

    # Load blocklist file entries (if configured)
    blocklist_file = Path(config.get("blocklist_file", ""))
    if blocklist_file.is_file():
        for line in blocklist_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                config["blocklist_patterns"].append(line)

    # Validate storage_mode
    if config["storage_mode"] not in ("smart", "visual", "both", "text"):
        config["storage_mode"] = "smart"

    return config


def save_config(data: dict) -> None:
    """Write config dict to disk. Only saves keys that differ from defaults."""
    try:
        APPDATA_DIR.mkdir(parents=True, exist_ok=True)
        # Save only non-default values to keep the file clean
        to_save = {}
        for key, val in data.items():
            if key in _DEFAULTS and val != _DEFAULTS[key]:
                to_save[key] = val
            elif key not in _DEFAULTS:
                to_save[key] = val  # unknown keys preserved
        CONFIG_FILE.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("Failed to save config.json")


def get(key: str, default=None):
    """Get a single config value (loads full config each time — use sparingly)."""
    cfg = load_config()
    return cfg.get(key, default)
