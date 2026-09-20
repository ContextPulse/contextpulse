# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Test-only helpers shared across the ContextPulse packages.

Why this lives in the installed package rather than in a conftest: a
conftest.py's fixtures are visible only to tests at or below its own
directory. Measured 2026-09-19 -- a fixture defined in
`packages/core/tests/conftest.py` and requested from a test under
`packages/screen/tests/` fails at setup with
`fixture '_probe_core_conftest_fixture' not found`. Since the config
unification makes screen/voice/touch tests exercise the real
`contextpulse_core.config` file path, they all need the same isolation
fixture, so it has to be importable rather than inherited:

    from contextpulse_core.testing import isolated_config  # noqa: F401

Importing the name into a test module (or into that package's conftest.py)
registers the fixture there.

This module imports pytest at module scope. That is deliberate and safe:
nothing in the production import graph imports `contextpulse_core.testing`,
and a missing pytest would fail loudly at import rather than silently
degrading a runtime path.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import contextpulse_core.config as cfg_mod


@pytest.fixture
def isolated_config(tmp_path, monkeypatch) -> Iterator[tuple[Path, Path]]:
    """Point contextpulse_core.config at a temp dir and clear its env vars.

    Yields ``(appdata_dir, config_file)``. Neither is created -- tests that
    want a pre-existing config.json write it themselves, or call
    ``save_config``, which creates the directory.

    Clears every variable in ``_ENV_MAP`` so a developer who has, say,
    CONTEXTPULSE_BLOCKLIST exported in their shell cannot turn a test green
    or red by accident. Also drops the parsed-config cache on both sides of
    the test, because that cache is keyed on (path, mtime_ns, size) and a
    test that writes a file fast enough after another one must not be able
    to inherit a neighbour's parse.
    """
    test_appdata = tmp_path / "ContextPulse"
    test_config = test_appdata / "config.json"

    monkeypatch.setattr(cfg_mod, "APPDATA_DIR", test_appdata)
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", test_config)

    for env_name in cfg_mod._ENV_MAP.values():
        monkeypatch.delenv(env_name, raising=False)

    cfg_mod.clear_config_cache()
    yield test_appdata, test_config
    cfg_mod.clear_config_cache()
