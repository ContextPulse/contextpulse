# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Tests for scripts/retention_sweep.py (cp-working-dir-4gb-unbounded).

scripts/ is not a package (no __init__.py), so the module under test is
loaded by file path, same pattern as tests/test_nightly_learning.py.

The Recycle Bin call (``SHFileOperationW``) is exercised for real against
``tmp_path`` fixtures — it is cheap, safe, and Windows-only, so mocking it
would only prove the mock was called, not that the OS call actually works.
"""

import importlib.util
import sys
import time
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "retention_sweep.py"


def _load_retention_sweep():
    spec = importlib.util.spec_from_file_location("retention_sweep_under_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec: retention_sweep.py defines a
    # frozen dataclass, and dataclasses' internal type resolution looks the
    # module up via sys.modules[cls.__module__] while the class body is
    # still executing. Without this line the lookup returns None and
    # dataclass() raises AttributeError on Python 3.14.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def rs():
    return _load_retention_sweep()


def _touch(path: Path, age_days: float, now: float) -> None:
    """Create a file and set its mtime to `age_days` days before `now`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    mtime = now - age_days * 86400
    import os
    os.utime(path, (mtime, mtime))


# ---------------------------------------------------------------------------
# scan() — classification
# ---------------------------------------------------------------------------

class TestScan:
    def test_old_item_is_swept(self, tmp_path, rs):
        now = time.time()
        _touch(tmp_path / "working" / "old_file.log", age_days=45, now=now)
        entries = rs.scan(tmp_path, targets=["working"], max_age_days=30, now=now)
        assert len(entries) == 1
        assert entries[0].sweep is True
        assert entries[0].protected is False

    def test_recent_item_is_kept(self, tmp_path, rs):
        now = time.time()
        _touch(tmp_path / "working" / "new_file.log", age_days=5, now=now)
        entries = rs.scan(tmp_path, targets=["working"], max_age_days=30, now=now)
        assert entries[0].sweep is False

    def test_item_exactly_at_threshold_is_swept(self, tmp_path, rs):
        """age_days >= max_age_days sweeps — boundary inclusive on the sweep side."""
        now = time.time()
        _touch(tmp_path / "working" / "boundary.log", age_days=30, now=now)
        entries = rs.scan(tmp_path, targets=["working"], max_age_days=30, now=now)
        assert entries[0].sweep is True

    def test_protected_pattern_never_swept_even_when_old(self, tmp_path, rs):
        now = time.time()
        _touch(tmp_path / "working" / "ep-2026-01-01-sample-episode" / "clip.wav", age_days=90, now=now)
        entries = rs.scan(
            tmp_path, targets=["working"], max_age_days=30, protect_patterns=["*sample-episode*"], now=now
        )
        assert len(entries) == 1
        assert entries[0].protected is True
        assert entries[0].sweep is False

    def test_no_protect_patterns_by_default(self, tmp_path, rs):
        """DEFAULT_PROTECT_PATTERNS ships empty -- this tool must never hardcode a
        real project's directory name in tracked source."""
        assert rs.DEFAULT_PROTECT_PATTERNS == []

    def test_directory_with_one_recent_file_is_not_swept(self, tmp_path, rs):
        """A directory's age is its NEWEST file's mtime, not the dir entry's own mtime --
        one fresh file inside protects the whole directory from being swept."""
        now = time.time()
        d = tmp_path / "working" / "mixed_age_dir"
        _touch(d / "old.log", age_days=90, now=now)
        _touch(d / "new.log", age_days=1, now=now)
        entries = rs.scan(tmp_path, targets=["working"], max_age_days=30, now=now)
        assert len(entries) == 1
        assert entries[0].sweep is False

    def test_missing_target_directory_is_silently_skipped(self, tmp_path, rs):
        """No 'working/' on disk at all must not raise -- e.g. after a prior sweep."""
        entries = rs.scan(tmp_path, targets=["working", "dist", "does_not_exist"])
        assert entries == []

    def test_empty_directory_list_returns_empty(self, tmp_path, rs):
        entries = rs.scan(tmp_path, targets=[])
        assert entries == []

    def test_size_bytes_reflects_actual_content(self, tmp_path, rs):
        now = time.time()
        f = tmp_path / "working" / "sized.log"
        _touch(f, age_days=45, now=now)
        f.write_text("x" * 1000)
        import os
        os.utime(f, (now - 45 * 86400, now - 45 * 86400))
        entries = rs.scan(tmp_path, targets=["working"], max_age_days=30, now=now)
        assert entries[0].size_bytes == 1000

    def test_multiple_targets_scanned_independently(self, tmp_path, rs):
        now = time.time()
        _touch(tmp_path / "working" / "a.log", age_days=45, now=now)
        _touch(tmp_path / "dist" / "b.log", age_days=45, now=now)
        entries = rs.scan(tmp_path, targets=["working", "dist"], max_age_days=30, now=now)
        assert len(entries) == 2
        swept_names = {e.path.name for e in entries if e.sweep}
        assert swept_names == {"a.log", "b.log"}


# ---------------------------------------------------------------------------
# format_report()
# ---------------------------------------------------------------------------

class TestFormatReport:
    def test_reports_total_reclaimable_only_counts_swept_items(self, rs):
        entries = [
            rs.SweepEntry(Path("a"), size_bytes=1_048_576, age_days=45, protected=False, sweep=True),
            rs.SweepEntry(Path("b"), size_bytes=2_097_152, age_days=5, protected=False, sweep=False),
            rs.SweepEntry(Path("c"), size_bytes=5_242_880, age_days=90, protected=True, sweep=False),
        ]
        report = rs.format_report(entries)
        assert "Total reclaimable: 1.0 MB across 1 item(s)" in report
        assert "PROTECTED" in report

    def test_empty_entries_reports_zero(self, rs):
        report = rs.format_report([])
        assert "Total reclaimable: 0.0 MB across 0 item(s)" in report


# ---------------------------------------------------------------------------
# send_to_recycle_bin() — real Windows Recycle Bin call against tmp_path
# ---------------------------------------------------------------------------

class TestSendToRecycleBin:
    def test_moves_real_file_out_of_place(self, tmp_path, rs):
        target = tmp_path / "throwaway.txt"
        target.write_text("delete me")
        ok = rs.send_to_recycle_bin(target)
        assert ok is True
        assert not target.exists()

    def test_moves_real_directory_out_of_place(self, tmp_path, rs):
        target = tmp_path / "throwaway_dir"
        target.mkdir()
        (target / "inner.txt").write_text("x")
        ok = rs.send_to_recycle_bin(target)
        assert ok is True
        assert not target.exists()

    def test_nonexistent_path_returns_false_not_raise(self, tmp_path, rs):
        ok = rs.send_to_recycle_bin(tmp_path / "never_existed.txt")
        assert ok is False


# ---------------------------------------------------------------------------
# main() — CLI contract
# ---------------------------------------------------------------------------

class TestMain:
    def test_dry_run_default_does_not_move_anything(self, tmp_path, rs, capsys):
        now = time.time()
        f = tmp_path / "working" / "stale.log"
        _touch(f, age_days=45, now=now)
        rc = rs.main(["--project-root", str(tmp_path), "--targets", "working"])
        assert rc == 0
        assert f.exists(), "dry-run must never move a file"
        out = capsys.readouterr().out
        assert "DRY RUN" in out

    def test_execute_sweeps_stale_and_spares_protected(self, tmp_path, rs, capsys):
        now = time.time()
        stale = tmp_path / "working" / "stale.log"
        _touch(stale, age_days=45, now=now)
        protected = tmp_path / "working" / "ep-2026-01-01-sample-episode" / "clip.wav"
        _touch(protected, age_days=90, now=now)
        rc = rs.main([
            "--project-root", str(tmp_path), "--targets", "working",
            "--protect", "*sample-episode*", "--execute",
        ])
        assert rc == 0
        assert not stale.exists(), "stale item must be swept under --execute"
        assert protected.exists(), "protected item must survive --execute regardless of age"

    def test_env_protect_patterns_merged_with_cli_protect(self, tmp_path, rs, capsys, monkeypatch):
        """CP_RETENTION_PROTECT env var patterns must be merged with --protect,
        not override it -- both sources of protection apply together."""
        now = time.time()
        stale = tmp_path / "working" / "stale.log"
        _touch(stale, age_days=45, now=now)
        env_protected = tmp_path / "working" / "env-protected-dir" / "clip.wav"
        _touch(env_protected, age_days=90, now=now)
        monkeypatch.setenv("CP_RETENTION_PROTECT", "*env-protected*")
        rc = rs.main(["--project-root", str(tmp_path), "--targets", "working", "--execute"])
        assert rc == 0
        assert not stale.exists(), "stale item must still be swept"
        assert env_protected.exists(), "env-var-protected item must survive --execute"

    def test_no_target_directories_present_reports_nothing_and_exits_zero(self, tmp_path, rs, capsys):
        rc = rs.main(["--project-root", str(tmp_path), "--targets", "working"])
        assert rc == 0
        assert "Nothing found" in capsys.readouterr().out
