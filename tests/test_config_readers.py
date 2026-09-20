# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""CI guard against dead and duplicate ContextPulse config fields.

Adopted from `scratchpad/audit_config_readers.py` (the 2026-09-19 audit's
cross-check script) per section 5 of
`.internal/audit-2026-09-19/spec-config-unification.md`. Changes from the
script, all specified there:

* the repo root is derived from ``__file__`` instead of a hardcoded path;
* the key list is imported from ``contextpulse_core.config._DEFAULTS`` rather
  than AST-walked (the AST string scan is kept for the *readers*, because it
  ignores comments);
* the ``get_config_schema()`` branch is gone -- that method is deleted by
  step 7 of the spec;
* **``contextpulse_core/settings.py`` is excluded from the reader scan.** This
  is the change that closes the audit script's stated limitation. The script
  could only find a key nobody reads; the larger defect family it missed was a
  key read *only by the Settings dialog that writes it* -- a closed loop that
  looks alive from the outside. Excluding the dialog makes those keys read as
  zero-reader, which is the truth about the daemon.

WHY THESE TESTS ARE `xfail(strict=True)` RIGHT NOW
--------------------------------------------------
This file lands in step 1/2 of the spec, before any reader is rewired. The
guard is therefore *expected to fail*, and strict xfail is what proves it can
detect the defect it exists to catch: if someone wires the readers, the test
starts passing and strict xfail turns that into a hard XPASS failure, forcing
the marker to be removed rather than quietly leaving a dead guard behind.

Pre-fix baseline, measured on branch feat/config-unification-core at the
commit that added this file (printed by the tests themselves, see the
`BASELINE` output line):

* dead keys: the MISLEADING rows 7-17 of `.internal/audit-2026-09-19/
  dead-controls.md` -- `auto_interval`, `storage_mode`, `jpeg_quality`,
  `buffer_max_age`, the four `hotkey_*`, `blocklist_patterns`,
  `always_both_apps`, `blocklist_file` -- plus the keys added in step 1 that
  have no reader yet (`auto_interval_idle`, `auto_idle_threshold`,
  `ocr_diff_threshold`) and the sight-side duplicates of row 30
  (`change_threshold`, `max_width`, `max_height`, `activity_max_age`, the
  three `event_*`). `memory_enabled`/`memory_tier`/`output_dir` are NOT in the
  baseline -- step 1 deleted them, which is the first reduction this guard
  can see.
* ungoverned env reads: `contextpulse_sight/config.py` (system B, deleted in
  spec step 4), `contextpulse_voice/config.py` and
  `contextpulse_touch/config.py` (their `_env`/`os.environ` fallbacks, deleted
  in step 7), and `daemon.py`'s own `CONTEXTPULSE_ACTIVITY_DB` (step 6).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from contextpulse_core.config import _DEFAULTS, _ENV_MAP

REPO_ROOT = Path(__file__).resolve().parents[1]

# The declaration site itself, and the dialog that writes the file. See the
# module docstring for why settings.py is excluded rather than scanned.
EXCLUDED_FROM_READER_SCAN = (
    "packages/core/src/contextpulse_core/config.py",
    "packages/core/src/contextpulse_core/settings.py",
)

# Env vars that are legitimately NOT user tunables: paths, diagnostics and
# per-process overrides that have no config.json key and no Settings control.
# Keyed by file so a NEW variable in an allowlisted file still trips the
# guard -- the point is that every addition is deliberate.
ENV_READ_ALLOWLIST: dict[str, set[str]] = {
    "packages/core/src/contextpulse_core/log_rotation.py": {
        "CONTEXTPULSE_LOG_MAX_BYTES",
        "CONTEXTPULSE_LOG_BACKUP_COUNT",
        "CONTEXTPULSE_LOG_REPEAT_WINDOW_SEC",
        "CONTEXTPULSE_LOG_REPEAT_THRESHOLD",
        "CONTEXTPULSE_LOG_REPEAT_EVERY",
    },
    "packages/core/src/contextpulse_core/_thread_caps.py": {"CONTEXTPULSE_CPU_THREADS"},
    "packages/core/src/contextpulse_core/probe.py": {
        "CONTEXTPULSE_ACTIVITY_DB",
        "CONTEXTPULSE_PROBE_DB",
        "CONTEXTPULSE_PROBE_PROMPT_FILE",
    },
    "packages/knowledge/src/contextpulse_knowledge/bridge.py": {
        "CONTEXTPULSE_ACTIVITY_DB",
        "CONTEXTPULSE_KNOWLEDGE_DB",
    },
    "packages/knowledge/src/contextpulse_knowledge/mcp_tools.py": {"CONTEXTPULSE_KNOWLEDGE_DB"},
    "packages/memory/src/contextpulse_memory/mcp_server.py": {"CONTEXTPULSE_MEMORY_DIR"},
    # A thread-budget diagnostic, the sibling of _thread_caps' variable --
    # not a tunable. The spec expected daemon.py to hold no env reads after
    # step 6; that refers to CONTEXTPULSE_ACTIVITY_DB (line 58), which is
    # deliberately NOT allowlisted here so it stays visible until step 6
    # replaces it with an import from core.config.
    "packages/core/src/contextpulse_core/daemon.py": {"CONTEXTPULSE_THREAD_BUDGET_WARN"},
}

# config.py declares the variables; it is the one file allowed to read them.
ENV_SCAN_EXCLUDED = ("packages/core/src/contextpulse_core/config.py",)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _is_test_path(rel: str) -> bool:
    return "/tests/" in f"/{rel}" or Path(rel).name.startswith("test_")


def _source_files() -> list[Path]:
    """Every production .py under packages/*/src.

    Deliberately not packages/*/build/lib or dist/: those are stale build
    artefacts, not source, and scanning them would let a deleted reader keep
    a key looking alive.
    """
    files: list[Path] = []
    for src_dir in sorted((REPO_ROOT / "packages").glob("*/src")):
        for py_file in src_dir.rglob("*.py"):
            if "__pycache__" in py_file.parts:
                continue
            rel = _rel(py_file)
            if _is_test_path(rel):
                continue
            files.append(py_file)
    return files


def _reader_constants(tree: ast.AST) -> set[str]:
    """String literals that could plausibly be a config READ.

    Excludes literals used as a key in a dict display. A read is spelled
    ``cfg["k"]`` / ``cfg.get("k")`` / ``cfg_get("k", default)``; a literal in
    ``{"k": value}`` position is an emitted payload or a schema declaration.
    Measured: without this, `storage_mode` counts as read because
    `sight_module.py:120` puts `"storage_mode": storage_mode` into an event
    payload -- a false negative that would hide one of the MISLEADING rows
    this guard exists to catch. (A read nested inside a dict VALUE, e.g.
    ``{"burst_timeout": cfg.get("touch_burst_timeout", ...)}``, is unaffected:
    only the key node is skipped.)
    """
    dict_keys = {
        id(key)
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if key is not None
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in dict_keys
    }


def _parse(py_file: Path) -> ast.AST | None:
    try:
        return ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """id()s of Constant nodes that are module/class/function docstrings.

    A docstring naming an env var is documentation, not a read.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _keys_without_readers() -> dict[str, int]:
    """{config key: number of production readers outside config.py/settings.py}."""
    counts = dict.fromkeys(_DEFAULTS, 0)
    for py_file in _source_files():
        if _rel(py_file) in EXCLUDED_FROM_READER_SCAN:
            continue
        src = py_file.read_text(encoding="utf-8")
        if not any(key in src for key in counts):  # cheap pre-filter
            continue
        tree = _parse(py_file)
        if tree is None:
            continue
        seen = _reader_constants(tree)
        for key in counts:
            if key in seen:
                counts[key] += 1
    return {k: v for k, v in counts.items() if v == 0}


def _ungoverned_env_reads() -> list[str]:
    """'file:line CONTEXTPULSE_X' for every namespaced env var read outside core config."""
    hits: list[str] = []
    for py_file in _source_files():
        rel = _rel(py_file)
        if rel in ENV_SCAN_EXCLUDED:
            continue
        src = py_file.read_text(encoding="utf-8")
        if "CONTEXTPULSE_" not in src:
            continue
        tree = _parse(py_file)
        if tree is None:
            continue
        allowed = ENV_READ_ALLOWLIST.get(rel, set())
        skip = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in skip:
                continue
            if not node.value.startswith("CONTEXTPULSE_"):
                continue
            if node.value in allowed:
                continue
            hits.append(f"{rel}:{node.lineno} {node.value}")
    return sorted(hits)


# ── The scan must not be vacuous ────────────────────────────────────────
# "0 findings" and "0 files examined" produce identical output; these pin
# the denominator so a broken glob cannot read as a clean bill of health.


def test_the_scan_actually_sees_the_source_tree():
    files = _source_files()
    assert len(files) > 50, f"only {len(files)} source files found under {REPO_ROOT}/packages/*/src"
    names = {_rel(f) for f in files}
    assert "packages/core/src/contextpulse_core/config.py" in names
    assert "packages/screen/src/contextpulse_sight/app.py" in names
    assert not any(_is_test_path(n) for n in names)
    assert not any("/build/" in n or "/dist/" in n for n in names)


def test_there_are_keys_to_check():
    assert len(_DEFAULTS) > 20, "the key list is empty or tiny; this guard would be vacuous"


# ── Consistency of the declaration itself (passes today) ────────────────


def test_every_env_map_key_is_declared_in_defaults():
    orphans = sorted(set(_ENV_MAP) - set(_DEFAULTS))
    assert not orphans, f"_ENV_MAP names keys that _DEFAULTS does not declare: {orphans}"


def test_every_env_var_is_namespaced():
    bad = {k: v for k, v in _ENV_MAP.items() if not v.startswith("CONTEXTPULSE_")}
    assert not bad, f"env vars outside the CONTEXTPULSE_ namespace: {bad}"


def test_no_two_keys_share_an_env_var():
    seen: dict[str, str] = {}
    clashes = []
    for key, var in _ENV_MAP.items():
        if var in seen:
            clashes.append((var, seen[var], key))
        seen[var] = key
    assert not clashes, f"one env var mapped to two keys: {clashes}"


# ── The guard proper (expected red until the readers are wired) ─────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Pre-fix baseline: the MISLEADING rows 7-17 of dead-controls.md plus the "
        "row-30 sight duplicates and the 3 keys added by spec step 1. Flips to a "
        "hard XPASS failure -- deliberately -- once spec steps 3-7 wire the readers."
    ),
)
def test_every_config_key_has_a_production_reader():
    dead = _keys_without_readers()
    print(f"\nBASELINE dead keys (no reader outside config.py/settings.py): {len(dead)}")
    for key in sorted(dead):
        print(f"  - {key}")
    assert not dead, (
        f"{len(dead)} config key(s) declared in _DEFAULTS with no production reader "
        f"outside contextpulse_core/config.py and settings.py: {sorted(dead)}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Pre-fix baseline: contextpulse_sight/config.py (system B), the voice/touch "
        "env fallbacks, and daemon.py's own CONTEXTPULSE_ACTIVITY_DB. Deleted by spec "
        "steps 4, 6 and 7; strict xfail forces the marker off when they go."
    ),
)
def test_only_core_config_reads_contextpulse_env_vars():
    hits = _ungoverned_env_reads()
    by_file: dict[str, int] = {}
    for hit in hits:
        by_file[hit.split(":")[0]] = by_file.get(hit.split(":")[0], 0) + 1
    print(f"\nBASELINE ungoverned CONTEXTPULSE_* reads: {len(hits)}")
    for name, count in sorted(by_file.items()):
        print(f"  - {name}: {count}")
    assert not hits, (
        f"{len(hits)} CONTEXTPULSE_* env var(s) read outside contextpulse_core/config.py. "
        f"Every tunable belongs in _DEFAULTS/_ENV_MAP; anything else must be added to "
        f"ENV_READ_ALLOWLIST deliberately, with a reason: {hits}"
    )
