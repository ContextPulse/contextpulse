# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Jerard Ventures LLC
"""scripts/publish_denylist.py refuses a push that would carry a denylisted term.

Every term here is synthetic. The real list is gitignored and never appears in
the repository, which is the point of it.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_denylist.py"
_spec = importlib.util.spec_from_file_location("publish_denylist_under_test", _SCRIPT)
pd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pd  # dataclasses resolve the module by name
_spec.loader.exec_module(pd)

ZERO = "0" * 40
TERM = "Quorlaxby"  # synthetic stand-in for a personal term


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return env


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        env=_env(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit(repo: Path, name: str, content: str, message: str) -> str:
    (repo / name).write_text(content, encoding="utf-8", newline="\n")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with one commit already on a fake remote 'origin', and a denylist."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    work.mkdir()
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(work, "init", "-q", "-b", "main")
    git(work, "remote", "add", "origin", str(remote))
    commit(work, "README.md", "hello\n", "chore: initial commit for the fixture")
    git(work, "push", "-q", "origin", "main")
    deny = work / ".internal" / "publish-denylist.txt"
    deny.parent.mkdir()
    deny.write_text(f"# comment\n\n{TERM.lower()}\t# T1 synthetic person\n", encoding="utf-8")
    return work


def push_line(repo: Path, branch: str = "main") -> str:
    sha = git(repo, "rev-parse", "HEAD")
    remote_sha = git(repo, "rev-parse", f"origin/{branch}") if branch == "main" else ZERO
    return f"refs/heads/{branch} {sha} refs/heads/{branch} {remote_sha}\n"


def test_clean_push_passes(repo: Path, capsys) -> None:
    commit(repo, "a.py", "x = 1\n", "feat: add a harmless constant")
    assert pd.main(["origin"], push_line(repo), repo) == 0
    assert "OK" in capsys.readouterr().out


def test_term_in_added_line_is_refused_case_insensitively(repo: Path, capsys) -> None:
    commit(repo, "a.py", f'QUERY = "dinner with {TERM.upper()}"\n', "test: add a fixture query")
    assert pd.main(["origin"], push_line(repo), repo) == 1
    err = capsys.readouterr().err
    assert "T1 synthetic person x1" in err
    assert TERM.lower() not in err.lower(), "a refusal must never echo the term"


def test_older_commit_under_a_clean_tip_is_refused(repo: Path, capsys) -> None:
    """The PR #31 shape: the tip scrubbed the term, an earlier commit still carries it."""
    leaked = commit(repo, "a.py", f'Q = "{TERM}"\n', "test: add the original fixture")
    commit(repo, "a.py", 'Q = "Zed"\n', "test: replace the name with a generic one")
    assert TERM.lower() not in (repo / "a.py").read_text().lower()
    assert pd.main(["origin"], push_line(repo), repo) == 1
    assert leaked[:10] in capsys.readouterr().err


def test_commit_held_only_by_another_remote_is_still_scanned(repo: Path, tmp_path: Path) -> None:
    """The private backup remote held the unscrubbed commits; that must not exempt them."""
    backup = tmp_path / "backup.git"
    git(tmp_path, "init", "-q", "--bare", str(backup))
    git(repo, "remote", "add", "backup", str(backup))
    commit(repo, "a.py", f'Q = "{TERM}"\n', "test: add the original fixture")
    git(repo, "push", "-q", "backup", "main")
    assert pd.main(["origin"], push_line(repo), repo) == 1


def test_term_in_commit_message_is_refused(repo: Path) -> None:
    commit(repo, "a.py", "x = 2\n", f"fix: handle the case {TERM} reported\n\n- bullet {TERM}")
    assert pd.main(["origin"], push_line(repo), repo) == 1


def test_term_in_a_new_empty_file_path_is_refused(repo: Path) -> None:
    (repo / f"notes-{TERM}").mkdir()
    commit(repo, f"notes-{TERM}/empty.txt", "", "docs: add an empty placeholder")
    assert pd.main(["origin"], push_line(repo), repo) == 1


def test_commits_already_on_the_destination_are_not_rescanned(repo: Path) -> None:
    commit(repo, "a.py", f'Q = "{TERM}"\n', "test: add the original fixture")
    git(repo, "push", "-q", "origin", "main")  # already public: nothing new to refuse
    commit(repo, "b.py", "y = 1\n", "feat: add an unrelated constant")
    assert pd.main(["origin"], push_line(repo), repo) == 0


def test_new_branch_push_scans_from_the_destination_refs(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "a.py", f'Q = "{TERM}"\n', "test: add the original fixture")
    assert pd.main(["origin"], push_line(repo, "feature"), repo) == 1


def test_deletion_sends_nothing(repo: Path) -> None:
    line = f"(delete) {ZERO} refs/heads/gone {git(repo, 'rev-parse', 'HEAD')}\n"
    assert pd.main(["origin"], line, repo) == 0


@pytest.mark.parametrize("content", [None, "# only comments\n\n"])
def test_missing_or_empty_denylist_fails_closed(repo: Path, content: str | None, capsys) -> None:
    deny = repo / ".internal" / "publish-denylist.txt"
    if content is None:
        deny.unlink()
    else:
        deny.write_text(content, encoding="utf-8")
    commit(repo, "a.py", "x = 1\n", "feat: add a harmless constant")
    assert pd.main(["origin"], push_line(repo), repo) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_linked_worktree_reads_the_main_worktree_list(repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "wt", str(wt))
    commit(wt, "a.py", f'Q = "{TERM}"\n', "test: add the original fixture")
    sha = git(wt, "rev-parse", "HEAD")
    assert pd.main(["origin"], f"refs/heads/wt {sha} refs/heads/wt {ZERO}\n", wt) == 1


def test_git_failure_fails_closed(repo: Path) -> None:
    bogus = "f" * 40
    assert pd.main(["origin"], f"refs/heads/main {bogus} refs/heads/main {ZERO}\n", repo) == 2


def test_no_remote_name_fails_closed(repo: Path) -> None:
    assert pd.main([], push_line(repo), repo) == 2
