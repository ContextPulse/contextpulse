# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Persistent configuration for all ContextPulse packages.

Reads from %APPDATA%/ContextPulse/config.json with env var overrides.
Env vars always win (backward compat with Sight's CONTEXTPULSE_* vars).
Missing keys in config.json are filled from _DEFAULTS on load.

This module is the ONE declaration site for every ContextPulse tunable
(spec: .internal/audit-2026-09-19/spec-config-unification.md). `_DEFAULTS`
declares the key and its default, `_ENV_MAP` declares its env var, `_CLAMPS`
declares its valid range. Path constants (OUTPUT_DIR, ACTIVITY_DB_PATH,
APPDATA_DIR, CONTEXTPULSE_HOME) are startup-bound and env-only: they are
module constants, not `_DEFAULTS` keys, because a dict copy of a path that
is resolved at import time can only ever disagree with the real one.

`load_config()` is called per frame / per OCR row once the readers are wired,
so it keeps an (mtime_ns, size) cache of the parsed JSON layer: a call costs
one stat() plus a small merge rather than a JSON parse. A parse failure
(corrupt file, or a reader catching a half-written one) returns the LAST GOOD
parsed layer rather than dropping every user override on the floor --
`save_config()` writes via a temp file + os.replace so that window should not
exist, but a config read that silently reverts to defaults would disable the
privacy blocklist, so both halves are belt and braces.
"""

import json
import logging
import math
import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

logger = logging.getLogger(__name__)

# The .env files this module actually merged into os.environ, in load order.
#
# Published because a second layer needs to know EXACTLY which files could
# have written a value here, and repeating the search elsewhere resolved a
# different file: load_dotenv() with no path is find_dotenv(usecwd=False),
# a walk up from THIS file's directory, while the obvious-looking
# find_dotenv(usecwd=True) walks up from the working directory. A checkout
# with a .env and a cwd with a .env are two different files. See
# mcp_auth._dotenv_sets_off(), which reads this list rather than searching.
#
# find_dotenv() is called here with the same (absent) arguments load_dotenv()
# would pass on, and from this same module, so the frame it inspects and the
# path it returns are the ones load_dotenv() would have used.
LOADED_DOTENV_PATHS: list[str] = []

# Load optional workspace-level .env from CONTEXTPULSE_DOTENV env var, then local overrides.
# This avoids hardcoding any specific user's directory structure.
_workspace_dotenv = os.environ.get("CONTEXTPULSE_DOTENV", "")
if _workspace_dotenv:
    load_dotenv(_workspace_dotenv, override=True)
    LOADED_DOTENV_PATHS.append(_workspace_dotenv)
_local_dotenv = find_dotenv()
if _local_dotenv:
    load_dotenv(_local_dotenv, override=True)  # local .env overrides everything
    LOADED_DOTENV_PATHS.append(_local_dotenv)


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
# Startup-bound and env-only on purpose -- see the module docstring. There is
# deliberately NO _DEFAULTS["output_dir"]: the dict copy had zero readers and
# could only drift from the constant every consumer actually imports.
_default_output = str(Path.home() / "Pictures" / "ContextPulse") if sys.platform == "darwin" else str(Path.home() / "screenshots")
OUTPUT_DIR = Path(env("CONTEXTPULSE_OUTPUT_DIR", _default_output))
ACTIVITY_DB_PATH = OUTPUT_DIR / env("CONTEXTPULSE_ACTIVITY_DB", "activity.db")

# ── Defaults ─────────────────────────────────────────────────────────
# These define every configurable setting and its default value.
# config.json stores user overrides; env vars override everything.
_DEFAULTS: dict = {
    # Sight — capture settings
    "auto_interval": 5,           # seconds (0 = disabled)
    "auto_interval_idle": 30,     # seconds; stretched interval while idle
    "auto_idle_threshold": 60,    # seconds without an event before going idle
    "buffer_max_age": 1800,       # seconds (30 min)
    "change_threshold": 0.5,      # % pixel difference for dedup
    "ocr_diff_threshold": 5.0,    # % pixel diff below which a frame skips OCR
    "max_width": 1280,
    "max_height": 720,
    # 90, not 75: 90 is what the capture pipeline has actually been using
    # (contextpulse_sight.config.JPEG_QUALITY), so unifying on it makes the
    # cutover a plumbing change rather than a quality change.
    "jpeg_quality": 90,
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

    # Privacy — clipboard capture on/off. There is deliberately no
    # "redact_clipboard_text" companion: clipboard redaction is unconditional
    # (see contextpulse_sight.clipboard). The only clipboard choice a user has
    # is whether it is captured at all.
    "clipboard_enabled": True,

    # Activity tracking
    "activity_max_age": 86400,    # seconds (24h)

    # Event-driven capture
    "event_poll_interval": 0.5,   # seconds
    "event_movement_threshold": 200,  # pixels
    "event_idle_threshold": 30,   # seconds

    # Knowledge graph (Phase 1) — when true, the KG MCP tools replace the
    # throwaway Phase-0 probe tools (facts_about / context_at). Default false
    # keeps the probe as the live provider until the save-gated cut-over.
    "knowledge_enabled": False,

    # Voice — dictation settings
    "voice_hotkey": "ctrl+space",
    "voice_fix_hotkey": "ctrl+shift+space",
    "voice_whisper_model": "small",    # base | small | medium | large
    "voice_always_use_llm": False,
    "voice_anthropic_api_key": "",

    # Touch — typing/mouse event shaping. Declared here (not in
    # contextpulse_touch.config) so the Settings dialog and get_touch_config()
    # read one declaration instead of two.
    "touch_burst_timeout": 1.5,       # seconds of silence to end a typing burst
    "touch_correction_window": 15.0,  # seconds after paste to watch for edits
    "touch_min_burst_chars": 3,       # minimum chars for a burst event
    "touch_mouse_debounce": 0.1,      # seconds between mouse events
}

# ── Env var mapping ──────────────────────────────────────────────────
# Maps config keys → CONTEXTPULSE_* env var names (backward compat).
_ENV_MAP: dict[str, str] = {
    "auto_interval": "CONTEXTPULSE_AUTO_INTERVAL",
    "auto_interval_idle": "CONTEXTPULSE_AUTO_INTERVAL_IDLE",
    "auto_idle_threshold": "CONTEXTPULSE_AUTO_IDLE_THRESHOLD",
    "buffer_max_age": "CONTEXTPULSE_BUFFER_MAX_AGE",
    "change_threshold": "CONTEXTPULSE_CHANGE_THRESHOLD",
    "ocr_diff_threshold": "CONTEXTPULSE_OCR_DIFF_THRESHOLD",
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
    "knowledge_enabled": "CONTEXTPULSE_KNOWLEDGE_ENABLED",
    "clipboard_enabled": "CONTEXTPULSE_CLIPBOARD_ENABLED",
    "touch_burst_timeout": "CONTEXTPULSE_TOUCH_BURST_TIMEOUT",
    "touch_correction_window": "CONTEXTPULSE_TOUCH_CORRECTION_WINDOW",
    "touch_min_burst_chars": "CONTEXTPULSE_TOUCH_MIN_BURST_CHARS",
    "touch_mouse_debounce": "CONTEXTPULSE_TOUCH_MOUSE_DEBOUNCE",
}

# ── Valid ranges ─────────────────────────────────────────────────────
# (min, max); None means unbounded on that side. Ported from
# contextpulse_sight/config.py, which clamped only env values -- these now
# apply to config.json values too, because this module is the only path a
# value can take and a hand-edited config.json is exactly as capable of
# holding jpeg_quality: 500 as an env var is.
_CLAMPS: dict[str, tuple[float | None, float | None]] = {
    "jpeg_quality": (1, 100),
    "auto_interval": (0, None),
    "auto_interval_idle": (1, None),
    "auto_idle_threshold": (1, None),
    "buffer_max_age": (0, None),
    "change_threshold": (0.0, None),
    "ocr_diff_threshold": (0.0, None),
    "event_poll_interval": (0.1, None),
    "event_movement_threshold": (50, None),
    "event_idle_threshold": (5, None),
    "activity_max_age": (0, None),
    # 16..16384 px. These were left out on the grounds that
    # contextpulse_sight.config never clamped them either -- but the clamp
    # table is also the only place a value gets COERCED, so leaving them out
    # meant a hand-edited {"max_width": "not-a-number"} travelled all the way
    # to capture._max_size(), where int() raised inside _downscale() once per
    # frame, absorbed by the capture loop's generic error counter. The old
    # system read these as int(_env(...)) at import: one loud crash at
    # startup, not a silent per-frame one.
    #
    # The bound is deliberately far wider than any real display: 16 px is
    # below any useful thumbnail, 16384 px is 2x the widest shipping monitor
    # (8K is 7680) and above the max texture size of current GPUs. It cannot
    # change a value anyone was actually using; it exists to make a garbage
    # value fail at load, once, with a WARNING naming the key.
    "max_width": (16, 16384),
    "max_height": (16, 16384),
}

_STORAGE_MODES = ("smart", "visual", "both", "text")

# ── Parsed-JSON cache ────────────────────────────────────────────────
# _CACHE holds the last SUCCESSFUL parse as (stat_key, parsed_dict);
# _FAILED_KEY holds the stat_key of a file that failed to parse, so the
# WARNING is logged once per distinct failure rather than once per read.
# Both are rebound as whole tuples (never mutated in place) so a concurrent
# reader on another daemon thread can only ever see a consistent pair.
_CACHE: tuple[tuple, dict] | None = None
_FAILED_KEY: tuple | None = None


def clear_config_cache() -> None:
    """Drop the parsed-config cache. Called by save_config; tests use it too."""
    global _CACHE, _FAILED_KEY
    _CACHE = None
    _FAILED_KEY = None


def _copy_layer(data: dict) -> dict:
    """Shallow copy that also copies list values.

    Without this, `config["blocklist_patterns"].append(...)` in the
    blocklist-file branch mutated the list object inside _DEFAULTS (or inside
    the cached JSON layer), so every subsequent load_config() in the process
    returned a blocklist that had grown by one more copy of the file.
    """
    return {k: list(v) if isinstance(v, list) else v for k, v in data.items()}


def _read_json_layer() -> dict:
    """Return the parsed config.json, cached on (path, mtime_ns, size)."""
    global _CACHE, _FAILED_KEY

    path = CONFIG_FILE
    try:
        st = path.stat()
    except (OSError, ValueError):
        return {}  # no file (or an unusable path): defaults + env only

    key = (str(path), st.st_mtime_ns, st.st_size)
    cache = _CACHE
    if cache is not None and cache[0] == key:
        return cache[1]
    # "Last good" is scoped to the same FILE. Without the path check, a
    # caller that repoints CONFIG_FILE (the test fixture does; a future
    # multi-profile daemon could) would be handed the previous file's values
    # as its own last-good.
    last_good = cache[1] if cache is not None and cache[0][0] == key[0] else None
    if key == _FAILED_KEY:
        # Same bytes we already failed on and already warned about.
        return last_good if last_good is not None else {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"top-level JSON value is {type(data).__name__}, expected object")
    except (OSError, ValueError) as exc:
        _FAILED_KEY = key
        if last_good is not None:
            logger.warning("config.json unreadable (%s) — keeping the last good values", exc)
            return last_good
        logger.warning("config.json unreadable (%s) — falling back to defaults", exc)
        return {}

    _CACHE = (key, data)
    return data


def _clamp(key: str, value, default):
    """Coerce `value` to the default's numeric type and clamp it to _CLAMPS.

    Two non-finite cases get their own handling because JSON can express both
    and neither behaves like a bad value:

    * **OverflowError** is an ArithmeticError, NOT a ValueError, so it was not
      caught below. `json.loads` turns `1e400` (and the bare literal
      `Infinity`) into `float("inf")`, and `int(float("inf"))` raises it --
      out of _clamp, out of load_config(), and into every cfg_get() in the
      process, once per frame and once per MCP result row.
    * **NaN** raises nothing at all for a float-typed key. `nan < lo` and
      `nan > hi` are both False, so it passed both comparisons below
      untouched and reached buffer.add(), where `diff_pct < nan` is likewise
      always False: the dedup gate silently off and every frame stored.
      `caster(value)` already rejects it for an int-typed key (ValueError).
    """
    lo, hi = _CLAMPS[key]
    caster = type(default)
    try:
        coerced = caster(value)
    except (TypeError, ValueError, OverflowError):
        logger.warning("config key %s: %r is not a %s — using default %r", key, value, caster.__name__, default)
        return default
    # isfinite only for floats: an int is always finite, and math.isfinite()
    # on an int too large for a double raises OverflowError of its own.
    if isinstance(coerced, float) and not math.isfinite(coerced):
        logger.warning("config key %s: %r is not a finite number — using default %r", key, value, default)
        return default
    if lo is not None and coerced < lo:
        coerced = caster(lo)
    if hi is not None and coerced > hi:
        coerced = caster(hi)
    return coerced


def _normalise(config: dict) -> None:
    """Clamp/normalise in place. Applies to json values and env values alike."""
    for key in _CLAMPS:
        config[key] = _clamp(key, config[key], _DEFAULTS[key])

    # storage_mode: lowercase BEFORE validating, so "Visual" is honoured
    # instead of silently reverting to "smart".
    mode = config.get("storage_mode")
    mode = mode.lower() if isinstance(mode, str) else ""
    config["storage_mode"] = mode if mode in _STORAGE_MODES else _DEFAULTS["storage_mode"]

    # always_both_apps is compared against a lowercased process name
    # (ocr_worker._process), so it must be lowercased on load.
    apps = config.get("always_both_apps")
    if isinstance(apps, list):
        config["always_both_apps"] = [str(a).strip().lower() for a in apps if str(a).strip()]
    else:
        logger.warning("config key always_both_apps: %r is not a list — using default", apps)
        config["always_both_apps"] = list(_DEFAULTS["always_both_apps"])

    patterns = config.get("blocklist_patterns")
    if isinstance(patterns, list):
        config["blocklist_patterns"] = [str(p).strip() for p in patterns if str(p).strip()]
    else:
        logger.warning("config key blocklist_patterns: %r is not a list — using default", patterns)
        config["blocklist_patterns"] = list(_DEFAULTS["blocklist_patterns"])


def _coerce_env_number(key: str, raw: str, default):
    """int()/float() an env var without letting a typo crash every reader."""
    caster = type(default)
    try:
        return caster(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r is not a %s — using %r", _ENV_MAP[key], raw, caster.__name__, default)
        return default


def load_config() -> dict:
    """Load config.json merged with defaults, then apply env var overrides."""
    config = _copy_layer(_DEFAULTS)

    # Layer 1: config.json overrides defaults
    config.update(_copy_layer(_read_json_layer()))

    # Layer 2: env vars override config.json
    for key, env_name in _ENV_MAP.items():
        val = os.environ.get(env_name)
        if val is None:
            continue
        default = _DEFAULTS[key]
        # bool MUST be tested before int: bool is a subclass of int, so
        # isinstance(True, int) is True and the int() branch would claim every
        # boolean key. CONTEXTPULSE_KNOWLEDGE_ENABLED=true then raised
        # ValueError: invalid literal for int() with base 10: 'true'
        # out of load_config(), taking down every caller of config.get() --
        # including the daemon's clipboard_enabled lookup at startup. The
        # numeric spellings were wrong too but silently: "1" yielded the int
        # 1 and "0" the int 0, never True/False.
        if isinstance(default, bool):
            config[key] = val.lower() in ("1", "true", "yes")
        elif isinstance(default, (int, float)):
            config[key] = _coerce_env_number(key, val, default)
        elif isinstance(default, list):
            config[key] = [p.strip() for p in val.split(",") if p.strip()]
        else:
            config[key] = val

    # Load blocklist file entries (if configured)
    blocklist_file = Path(str(config.get("blocklist_file", "")))
    if blocklist_file.is_file():
        try:
            lines = blocklist_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            logger.warning("blocklist_file %s could not be read", blocklist_file, exc_info=True)
            lines = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                config["blocklist_patterns"].append(line)

    _normalise(config)
    return config


def save_config(data: dict) -> None:
    """Write config dict to disk. Only saves keys that differ from defaults.

    Atomic: writes a sibling .json.tmp and os.replace()s it into place, so a
    concurrent reader (the daemon and the MCP server both read this file, from
    different processes) can never observe a half-written file and fall back to
    an empty blocklist for the duration of one capture.
    """
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    try:
        APPDATA_DIR.mkdir(parents=True, exist_ok=True)
        # Save only non-default values to keep the file clean
        to_save = {}
        for key, val in data.items():
            if key in _DEFAULTS and val != _DEFAULTS[key]:
                to_save[key] = val
            elif key not in _DEFAULTS:
                to_save[key] = val  # unknown keys preserved
        tmp.write_text(json.dumps(to_save, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_FILE)
    except Exception:
        logger.exception("Failed to save config.json")
    finally:
        # os.replace consumed it on the success path; this only fires when the
        # write or the replace raised, and it must never mask that error.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not remove %s", tmp, exc_info=True)
        clear_config_cache()


def get(key: str, default=None):
    """Get a single config value.

    Cheap: the parsed JSON layer is cached on (mtime_ns, size), so a call is
    one stat() plus a dict merge. Safe to call per frame / per row.
    """
    cfg = load_config()
    return cfg.get(key, default)
