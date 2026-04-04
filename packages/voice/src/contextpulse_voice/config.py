# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Voice-specific configuration — reads from ContextPulse shared config.

Voice settings are stored in the shared %APPDATA%/ContextPulse/config.json
alongside Sight and other package settings. Env vars override everything.
"""

import os

from contextpulse_core.config import APPDATA_DIR, load_config

# Voice data directory (vocabulary, learned corrections, models, history)
VOICE_DATA_DIR = APPDATA_DIR / "voice"

# Model cache directory
MODEL_DIR = VOICE_DATA_DIR / "models"

# Vocabulary files
VOCAB_FILE = VOICE_DATA_DIR / "vocabulary.json"
LEARNED_VOCAB_FILE = VOICE_DATA_DIR / "vocabulary_learned.json"
CONTEXT_VOCAB_FILE = VOICE_DATA_DIR / "vocabulary_context.json"
USER_PROFILE_FILE = VOICE_DATA_DIR / "user_profile.json"


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def get_voice_config() -> dict:
    """Load voice-specific settings from shared ContextPulse config."""
    cfg = load_config()
    return {
        "hotkey": cfg.get("voice_hotkey", _env("CONTEXTPULSE_VOICE_HOTKEY", "ctrl+space")),
        "fix_hotkey": cfg.get("voice_fix_hotkey", _env("CONTEXTPULSE_VOICE_FIX_HOTKEY", "ctrl+shift+space")),
        "whisper_model": cfg.get("voice_whisper_model", _env("CONTEXTPULSE_VOICE_MODEL", "base")),
        "always_use_llm": cfg.get("voice_always_use_llm", False),
        "anthropic_api_key": cfg.get("voice_anthropic_api_key", ""),
    }


def get_api_key() -> str:
    """Return Anthropic API key — config first, env var fallback."""
    cfg = get_voice_config()
    key = cfg.get("anthropic_api_key", "")
    if key:
        return key
    return os.getenv("ANTHROPIC_API_KEY", "")


def has_api_key() -> bool:
    """Check if an API key is available from any source."""
    return bool(get_api_key())


def ensure_voice_dirs() -> None:
    """Create voice data directories if they don't exist."""
    VOICE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
