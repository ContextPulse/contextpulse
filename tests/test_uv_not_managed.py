# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""``uv run`` must never lock or sync this project's environment.

Measured 2026-10-25. This repo has never had a ``uv.lock``: the development
venv is built from per-package editable installs, which no single lock of the
root distribution describes. A ``uv run pytest`` at 00:46 that night CREATED a
lock (fresh resolve, newest everything) and then exact-synced the live venv to
it -- the venv the capture daemon runs from. It uninstalled numpy, mcp,
python-dotenv, cryptography, anthropic, onnxruntime and rapidocr before failing
on a DLL the running daemon held. The running processes hid the damage; the
next restart of the daemon or the MCP server would have failed on import.

``[tool.uv] managed = false`` makes ``uv run`` leave the project alone: no lock
is written and nothing is installed or removed. Checked before shipping against
a throwaway project both ways: without the setting ``uv run`` created
``uv.lock`` and installed the declared dependency; with it, neither happened.
"""

from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_marks_project_unmanaged_by_uv() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data.get("tool", {}).get("uv", {}).get("managed") is False, (
        "pyproject.toml must keep [tool.uv] managed = false, or any `uv run` "
        "will lock and exact-sync the live development venv"
    )


def test_no_uv_lock_is_tracked() -> None:
    assert not (ROOT / "uv.lock").exists(), (
        "uv.lock describes an environment this repo does not use; a sync to it "
        "uninstalls the editable contextpulse-* packages"
    )
