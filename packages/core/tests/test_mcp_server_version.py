# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""The unified MCP server must report ContextPulse's version, not the SDK's.

FastMCP takes no version argument, so the low-level server fell back to the
installed `mcp` package's version: the 0.1.1 installer answered `initialize`
with serverInfo.version "1.30.0". These tests read the value from the server's
own initialization options, which is what it sends in InitializeResult.
"""

from importlib.metadata import version as pkg_version
from pathlib import Path

import pytest
import tomllib
from contextpulse_core import __version__, mcp_unified

pytestmark = pytest.mark.unit

ROOT_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def _sent_version() -> str:
    app = mcp_unified.build_app("127.0.0.1", 8420)
    return app._mcp_server.create_initialization_options().server_version


def test_server_version_is_contextpulse_version():
    assert _sent_version() == __version__


def test_server_version_is_not_the_mcp_sdk_version():
    # The exact defect: the SDK's own version leaking out as ours.
    assert _sent_version() != pkg_version("mcp")


def test_core_version_matches_the_release_version():
    # The installer is versioned from the root pyproject; the server reports
    # contextpulse_core.__version__. They must move together.
    with ROOT_PYPROJECT.open("rb") as fh:
        release = tomllib.load(fh)["project"]["version"]
    assert __version__ == release
