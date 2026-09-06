# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Regression guard for contextpulse.spec (the PyInstaller build definition).

contextpulse.spec is not an importable module -- it runs inside PyInstaller's
injected namespace (SPECPATH, Analysis, EXE, ...) -- so it cannot be unit
tested by import. These tests read it as text and assert every first-party
package under packages/ that daemon.py can reach at runtime is represented in
pathex + hiddenimports, and that any package shipping a non-.py resource
loaded via importlib.resources has that resource in datas.

Regression for: contextpulse.spec listed pathex/hiddenimports for core,
screen, voice, touch, project, memory, agent but omitted knowledge entirely.
daemon.py's _init_knowledge() does `from contextpulse_knowledge.bridge import
...` gated on config.knowledge_enabled (default False) -- so a frozen build
never crashes, it just silently drops the Phase-1 KG spine the moment someone
flips the flag on a packaged install, with the failure recorded only in a
per-module error dict nobody is guaranteed to read. Found 2026-09-06; the
package had existed and been wired into daemon.py/mcp_unified.py for weeks
before this test existed.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "contextpulse.spec"


def _spec_text() -> str:
    assert SPEC_PATH.exists(), f"contextpulse.spec not found at {SPEC_PATH}"
    return SPEC_PATH.read_text(encoding="utf-8")


# Packages under packages/ that are (a) git-tracked (have a pyproject.toml)
# and (b) imported, directly or transitively, from contextpulse_core.daemon
# or contextpulse_core.mcp_unified -- i.e. reachable from the shipped entry
# point. `agent` is deliberately excluded from the reachability list below:
# it is listed in pathex today but nothing in daemon.py/mcp_unified.py
# imports it, so it is out of scope for this regression test.
REACHABLE_PACKAGES = [
    "core",
    "screen",
    "voice",
    "touch",
    "project",
    "memory",
    "knowledge",
]


def test_every_reachable_package_has_a_pathex_entry() -> None:
    text = _spec_text()
    missing = [
        pkg
        for pkg in REACHABLE_PACKAGES
        if f'"{pkg}" / "src"' not in text and f"'{pkg}' / 'src'" not in text
    ]
    assert not missing, (
        f"contextpulse.spec pathex is missing packages/{{{','.join(missing)}}}/src -- "
        "a frozen build cannot import these packages at all, regardless of "
        "hiddenimports, because PyInstaller's Analysis never sees the source tree."
    )


def test_knowledge_hidden_imports_present() -> None:
    """The Phase-1 KG spine package must be a real hidden import, not just on pathex.

    pathex alone lets PyInstaller's static analyzer FIND the package if
    something in the scanned code imports it unconditionally. daemon.py's
    import is inside a try/except gated on a config flag that defaults to
    False, so static analysis never sees it fire -- it must be forced via
    hiddenimports or it is silently dropped from the frozen build.
    """
    text = _spec_text()
    required = [
        "contextpulse_knowledge",
        "contextpulse_knowledge.bridge",
        "contextpulse_knowledge.cp_core",
        "contextpulse_knowledge.mcp_tools",
        "contextpulse_knowledge.migrate",
        "contextpulse_knowledge.store_sqlite",
    ]
    missing = [name for name in required if f'"{name}"' not in text]
    assert not missing, (
        f"contextpulse.spec hidden_imports is missing {missing} -- a packaged "
        "build with knowledge_enabled=true would fail ImportError on first "
        "use, caught silently by daemon.py's per-module error handling."
    )


def test_knowledge_schema_sql_is_a_packaged_data_file() -> None:
    """schema.sql is read via importlib.resources at runtime (migrate.py:
    read_schema_sql()), which PyInstaller's import analysis cannot discover --
    unlike a plain `import`, there is no name to trace. It must be listed in
    `datas` explicitly or KnowledgeStore.__init__ raises FileNotFoundError on
    first run of a frozen build.
    """
    text = _spec_text()
    assert "schema.sql" in text, (
        "contextpulse.spec datas is missing contextpulse_knowledge/schema.sql -- "
        "read via importlib.resources.files('contextpulse_knowledge').joinpath("
        "'schema.sql'), which PyInstaller cannot auto-detect."
    )


def test_all_new_hidden_imports_actually_resolve() -> None:
    """The hiddenimports list is a claim that these modules exist and import
    cleanly. A typo'd module name would pass the string-presence checks above
    while still being wrong. Prove each one imports for real, using the same
    pathex the spec constructs (packages/knowledge/src on sys.path).
    """
    import importlib
    import sys

    kg_src = str(REPO_ROOT / "packages" / "knowledge" / "src")
    added = kg_src not in sys.path
    if added:
        sys.path.insert(0, kg_src)
    try:
        for name in (
            "contextpulse_knowledge",
            "contextpulse_knowledge.bridge",
            "contextpulse_knowledge.cp_core",
            "contextpulse_knowledge.mcp_tools",
            "contextpulse_knowledge.migrate",
            "contextpulse_knowledge.store_sqlite",
        ):
            importlib.import_module(name)
    finally:
        if added:
            sys.path.remove(kg_src)


def test_knowledge_schema_sql_file_exists_at_the_path_the_spec_computes() -> None:
    """The spec computes this path as PACKAGES / "knowledge" / "src" /
    "contextpulse_knowledge" / "schema.sql" where PACKAGES = SPECPATH /
    "packages". Assert that exact path resolves on disk, not just that the
    string "schema.sql" appears somewhere in the spec text.
    """
    schema_path = (
        REPO_ROOT / "packages" / "knowledge" / "src" / "contextpulse_knowledge" / "schema.sql"
    )
    assert schema_path.exists(), f"expected schema.sql at {schema_path}, not found"
