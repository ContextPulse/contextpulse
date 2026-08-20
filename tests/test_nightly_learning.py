# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Regression tests for scripts/nightly_learning.py's exit-code contract.

The module docstring promises exit 1 for "partial failure (some modules
errored)". Before this fix, consolidate_vocabulary() always returned a
summary with no way to signal a step's exception, so main() unconditionally
printed status="success" and returned 0 even when every step had raised
(cp-consolidator-silent-step-failures).

scripts/ is not a package (no __init__.py), so the module under test is
loaded by file path rather than imported normally.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "nightly_learning.py"


def _load_nightly_learning():
    spec = importlib.util.spec_from_file_location("nightly_learning_under_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def nightly_learning():
    return _load_nightly_learning()


class _FakePath:
    """Stand-in for ACTIVITY_DB_PATH — Path.exists() can't be monkeypatched
    on a real WindowsPath instance (it's a read-only descriptor)."""

    def __init__(self, exists: bool):
        self._exists = exists

    def exists(self) -> bool:
        return self._exists


def _patch_common(monkeypatch, module, activity_db_exists=True):
    monkeypatch.setattr(module, "ACTIVITY_DB_PATH", _FakePath(activity_db_exists))
    monkeypatch.setattr(module, "_vocab_size", lambda path: 0)


class TestNightlyLearningExitCode:
    def test_clean_run_exits_zero_and_reports_success(self, nightly_learning, monkeypatch, capsys):
        _patch_common(monkeypatch, nightly_learning)
        monkeypatch.setattr(
            nightly_learning,
            "consolidate_vocabulary",
            lambda **kwargs: {
                "session_learned": 2, "cross_modal": 0, "ocr_harvested": 0,
                "clipboard_harvested": 0, "escalated": 0, "context_rebuilt": 0,
                "deduped": 0, "errors": {},
            },
        )

        rc = nightly_learning.main()

        assert rc == 0
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "success"
        assert "errors" not in result

    def test_step_failure_exits_one_and_reports_partial_failure(self, nightly_learning, monkeypatch, capsys):
        """The core regression: a step's exception must flip both the exit
        code and the reported status, not just get logged and vanish."""
        _patch_common(monkeypatch, nightly_learning)
        monkeypatch.setattr(
            nightly_learning,
            "consolidate_vocabulary",
            lambda **kwargs: {
                "session_learned": 0, "cross_modal": 0, "ocr_harvested": 0,
                "clipboard_harvested": 0, "escalated": 0, "context_rebuilt": 0,
                "deduped": 0, "errors": {"session_learning": "boom"},
            },
        )

        rc = nightly_learning.main()

        assert rc == 1
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "partial_failure"
        assert result["errors"] == {"session_learning": "boom"}

    def test_missing_activity_db_exits_two(self, nightly_learning, monkeypatch, capsys):
        _patch_common(monkeypatch, nightly_learning, activity_db_exists=False)

        rc = nightly_learning.main()

        assert rc == 2
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "error"
