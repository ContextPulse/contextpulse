# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""AT-0 — purity gate. AST-walk cp_core (+ extractors) imports; assert the
import set is a subset of the allowlist (§1)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "contextpulse_knowledge"

ALLOWLIST = {
    "dataclasses",
    "typing",
    "enum",
    "json",
    "hashlib",
    "math",
    "re",
    "unicodedata",
    "itertools",
    "bisect",
    "__future__",
}

# Files subject to the purity gate (cp_core + any extractors modules).
PURE_FILES = [SRC / "cp_core.py"]
extractors_dir = SRC / "extractors"
if extractors_dir.exists():
    PURE_FILES.extend(sorted(extractors_dir.glob("*.py")))


def _top_module(name: str) -> str:
    return name.split(".", 1)[0]


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(_top_module(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # relative import (e.g. extractors importing cp_core) — must stay
                # inside the pure package; flag as a same-package import, allowed
                # only if the target is itself pure. We treat "" (relative) as
                # allowed since purity of the target is enforced by its own test.
                mods.add("__relative__")
            elif node.module:
                mods.add(_top_module(node.module))
    return mods


@pytest.mark.parametrize("path", PURE_FILES, ids=[p.name for p in PURE_FILES])
def test_pure_imports_subset_of_allowlist(path: Path) -> None:
    mods = _imports_of(path)
    mods.discard("__relative__")  # relative imports resolved within the pure pkg
    illegal = mods - ALLOWLIST
    assert not illegal, f"{path.name} imports outside the purity allowlist: {sorted(illegal)}"


def test_cp_core_has_no_forbidden_tokens() -> None:
    """Belt-and-suspenders: scan for forbidden module names as identifiers."""
    text = (SRC / "cp_core.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    forbidden = {"sqlite3", "os", "pathlib", "numpy", "onnxruntime", "datetime", "time"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {_top_module(a.name) for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(_top_module(node.module))
    assert not (imported & forbidden), f"cp_core imports forbidden: {imported & forbidden}"
