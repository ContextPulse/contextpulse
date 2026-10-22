# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Every manifest bounds the Anthropic SDK below 2, and the code runs on what they admit.

Measured 2026-10-22. anthropic 2.0.0 shipped on 2026-10-21 with breaking
changes. Three facts were true that day:

- the ``pipeline`` extra declared ``anthropic>=0.39`` with no upper bound; that
  spec on its own resolves 2.1.0;
- the main dependency and ``packages/voice`` declared ``<1``, a cap added on
  2026-09-05 as "next major" from the 0.39 floor, although 1.0.0 had shipped on
  2026-08-20. Under it a fresh install resolved 0.125.0;
- the venv on David's machine has run anthropic 1.4.0 since 2026-09-07, which
  that ``<1`` cap forbids. pip did not object because the editable install's
  metadata predates the cap.

So the version David runs and the version a fresh install gets had parted. The
call sites (``llm_backend.run_api``, ``cleanup``, ``analyzer``, ``synthesize``)
pass none of the parameters 1.0 removed (temperature, top_p, top_k), so ``<2``
is the bound the code has actually been run on.

The contract test lives here rather than in packages/voice/tests because that
tree's conftest replaces ``anthropic`` with a MagicMock for every test in it.
"""

import re
import socket
from importlib import metadata
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import tomllib
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = [ROOT / "pyproject.toml", *sorted((ROOT / "packages").glob("*/pyproject.toml"))]


def _anthropic_specs() -> list[tuple[str, Requirement]]:
    found = []
    for path in MANIFESTS:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        project = data.get("project", {})
        groups = [("dependencies", project.get("dependencies", []))]
        groups += [(f"extra {k}", v) for k, v in project.get("optional-dependencies", {}).items()]
        for where, deps in groups:
            for dep in deps:
                req = Requirement(dep)
                if req.name == "anthropic":
                    found.append((f"{path.relative_to(ROOT).as_posix()} {where}", req))
    return found


def test_the_manifests_declare_anthropic_somewhere():
    # A scan that finds nothing would pass every test below. Three specs: the
    # main dependency, the pipeline extra, packages/voice.
    assert len(_anthropic_specs()) >= 3


@pytest.mark.parametrize("where,req", _anthropic_specs(), ids=lambda x: str(x))
def test_every_anthropic_spec_excludes_2(where, req):
    assert not req.specifier.contains(Version("2.0.0")), f"{where}: {req} admits anthropic 2.0.0"


@pytest.mark.parametrize("where,req", _anthropic_specs(), ids=lambda x: str(x))
def test_every_anthropic_spec_admits_the_1x_line_that_runs_live(where, req):
    assert req.specifier.contains(Version("1.4.0")), (
        f"{where}: {req} forbids 1.4.0, the live version"
    )


def test_the_installed_sdk_satisfies_every_manifest():
    """Catches the drift that went unseen for six weeks: a venv outside its own pins."""
    try:
        installed = Version(metadata.version("anthropic"))
    except metadata.PackageNotFoundError:
        pytest.fail("anthropic is a main dependency and is not installed")
    bad = [
        f"{where}: {req}"
        for where, req in _anthropic_specs()
        if not req.specifier.contains(installed)
    ]
    assert not bad, f"installed anthropic {installed} is outside: {bad}"


def test_dependabot_ignores_the_same_major_the_manifests_cap():
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    block = re.search(r'dependency-name:\s*"anthropic"\s*\n\s*versions:\s*\[([^\]]*)\]', text)
    assert block, "dependabot.yml has no anthropic ignore rule"
    assert block.group(1).strip() == '">=2.0.0"', f"anthropic ignore is {block.group(1)}"


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_run_api_builds_a_request_the_installed_sdk_accepts(monkeypatch):
    """The real run_api against the real SDK, pointed at a closed local port.

    Every llm_backend test in packages/voice/tests runs against a mocked SDK,
    so none of them can see a release that renames or removes a parameter.
    Here the SDK builds and validates the request and then fails to connect:
    a working call reports ``api APIConnectionError`` (or its subclass
    ``APITimeoutError``, which is what Windows gives for a closed localhost
    port); a signature break reports ``api TypeError`` or similar. Nothing leaves the machine and no
    key is used. Run it against a candidate SDK before lifting the bound.
    """
    import anthropic
    from contextpulse_voice.llm_backend import run_api

    assert not isinstance(anthropic, MagicMock), (
        "anthropic is mocked in this process; the test proves nothing"
    )
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{_closed_port()}")
    result = run_api("system", "hello", model="haiku", api_key="sk-test-not-a-key", timeout=0.5)
    assert result.ok is False
    assert result.error in {"api APIConnectionError", "api APITimeoutError"}, (
        f"anthropic {anthropic.__version__} rejected the call before sending it: {result.error}"
    )
