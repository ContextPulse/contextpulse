# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Jerard Ventures LLC
"""Refuse a push whose new commits carry a term on the private publish denylist.

Called by scripts/pre-push-gate.sh for every push to a remote that is not
proven private, with the pre-push hook's stdin lines
("<local ref> <local sha> <remote ref> <remote sha>") on stdin and the remote
name as the first argument.

What it scans: EVERY commit the push would send -- each commit reachable from
the pushed sha that no ref of the destination remote already holds -- not only
the tip. For each such commit it reads the full message, every added diff line,
and every new or renamed path. An older commit carrying a term under a clean
tip is refused, because the remote would receive that commit too.

Why (2026-11-09): a weekly batch was pushed from a branch that still carried
the original versions of commits an earlier batch had published with generic
names. The tip's content was clean and every content check passed: they look
for keys, emails and paths, and none of them knows which ordinary words are
personal. The private remote already held those commits, so "not on any remote"
would have exempted them too; the exclusion is the DESTINATION remote only.

The list: .internal/publish-denylist.txt in the main worktree (gitignored --
listing the terms in the repo would publish them). One term per line, matched
case-insensitively as a substring; blank lines and lines starting with # are
skipped; text after a TAB followed by # is a label, printed instead of the term
so a refusal never echoes the term into a log or a transcript.

Fails closed: a missing or empty list, or a git command that fails, refuses the
push (exit 2). There is deliberately no flag or variable that points the check
at another list -- a caller that can choose the list can choose an empty one.

Exit 0: nothing found. Exit 1: a term was found. Exit 2: the check could not run.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ZERO = "0" * 40
DENYLIST_RELPATH = Path(".internal") / "publish-denylist.txt"
_COMMIT_MARK = "\x00CPDENY-COMMIT "
_COMMIT_FMT = "%x00CPDENY-COMMIT "  # the same marker as git is asked for it (argv cannot carry NUL)


@dataclass(frozen=True)
class Term:
    """One denylisted term (lowercased) and the label shown in its place."""

    text: str
    label: str


class CheckError(Exception):
    """The check could not run; the caller must refuse the push."""


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise CheckError(f"git {args[0]} failed: {detail.strip()[-300:]}") from exc
    return result.stdout


def denylist_path(repo: Path) -> Path:
    """The list lives in the MAIN worktree, so a linked worktree is checked too."""
    common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    return common.parent / DENYLIST_RELPATH


def load_terms(path: Path) -> list[Term]:
    """Parse the list. A missing or empty list is an error, never a pass."""
    if not path.is_file():
        raise CheckError(f"denylist not found at {path}")
    terms: list[Term] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        text, _, label = raw.partition("\t#")
        text = text.strip().lower()
        if text:
            terms.append(Term(text=text, label=label.strip() or f"line {n}"))
    if not terms:
        raise CheckError(f"denylist at {path} has no terms")
    return terms


def outgoing_commits(repo: Path, remote: str, local_sha: str, remote_sha: str) -> list[str]:
    """Commits reachable from local_sha that no ref of ``remote`` already holds."""
    exclude = [f"--remotes={remote}"]
    if remote_sha and remote_sha != ZERO:
        # An unknown remote sha (remote moved since the last fetch) cannot be an
        # ancestor of local_sha, so it excludes nothing; rev-list would die on it.
        try:
            _git(repo, "cat-file", "-e", f"{remote_sha}^{{commit}}")
            exclude.insert(0, remote_sha)
        except CheckError:
            pass
    return _git(repo, "rev-list", local_sha, "--not", *exclude).split()


def commit_text(repo: Path, shas: list[str]) -> dict[str, str]:
    """Message + added lines + new paths per commit, lowercased."""
    if not shas:
        return {}
    texts: dict[str, list[str]] = {sha: [] for sha in shas}
    # Messages and diffs are read in two passes so a message line that happens
    # to look like diff syntax can never be mistaken for one and dropped.
    messages = _git(repo, "log", "--no-walk=unsorted", f"--format={_COMMIT_FMT}%H%n%B", *shas)
    for block in messages.split(_COMMIT_MARK)[1:]:
        sha, _, body = block.partition("\n")
        texts.setdefault(sha.strip(), []).append(body)
    diffs = _git(
        repo,
        "log",
        "--no-walk=unsorted",
        "-p",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames",
        "--cc",  # merges: only what the merge itself resolved, not both sides again
        f"--format={_COMMIT_FMT}%H",
        *shas,
    )
    for block in diffs.split(_COMMIT_MARK)[1:]:
        sha, _, diff = block.partition("\n")
        kept = texts.setdefault(sha.strip(), [])
        for line in diff.splitlines():
            # "+..." added lines (and "+++ b/<new path>"). The "diff --git" header
            # names the path of an empty or binary new file, which has no "+++"
            # line; renames and copies name the new path only in their headers.
            if line.startswith(
                ("+", "diff --git ", "diff --cc ", "rename to ", "copy to ", "Binary files ")
            ):
                kept.append(line)
    return {sha: "\n".join(parts).lower() for sha, parts in texts.items()}


def scan(repo: Path, remote: str, stdin_text: str, terms: list[Term]) -> dict[str, dict[str, int]]:
    """Return {short sha: {label: count}} for every outgoing commit with a hit."""
    shas: list[str] = []
    seen: set[str] = set()
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            if line.strip():
                raise CheckError(f"unexpected pre-push input line: {line!r}")
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = parts
        if local_sha == ZERO:
            continue  # a deletion sends no content
        for sha in outgoing_commits(repo, remote, local_sha, remote_sha):
            if sha not in seen:
                seen.add(sha)
                shas.append(sha)
    hits: dict[str, dict[str, int]] = {}
    for sha, text in commit_text(repo, shas).items():
        found = {}
        for term in terms:
            count = text.count(term.text)
            if count:
                found[term.label] = found.get(term.label, 0) + count
        if found:
            hits[sha[:10]] = found
    return hits


def main(argv: list[str], stdin_text: str, repo: Path) -> int:
    remote = argv[0] if argv else ""
    if not remote:
        print("publish-denylist: no remote name given; refusing.", file=sys.stderr)
        return 2
    try:
        terms = load_terms(denylist_path(repo))
        hits = scan(repo, remote, stdin_text, terms)
    except CheckError as exc:
        print(f"PRE-PUSH REFUSED -- publish-denylist check could not run: {exc}", file=sys.stderr)
        print(
            f"  Restore {DENYLIST_RELPATH.as_posix()} in the main worktree, then push again.",
            file=sys.stderr,
        )
        return 2
    if not hits:
        print(f"publish-denylist: OK, {len(terms)} term(s), no outgoing commit carries one.")
        return 0
    print(
        "PRE-PUSH REFUSED -- outgoing commits carry terms on the publish denylist", file=sys.stderr
    )
    print(f"({DENYLIST_RELPATH.as_posix()}). Terms are shown by label only:", file=sys.stderr)
    for sha, found in sorted(hits.items()):
        labels = ", ".join(f"{label} x{n}" for label, n in sorted(found.items()))
        print(f"  {sha}: {labels}", file=sys.stderr)
    print(
        "  Rewrite those commits with generic names (or publish a scrubbed copy),", file=sys.stderr
    )
    print(
        "  then push again. A clean tip is not enough: every listed commit would be sent.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin.read() if not sys.stdin.isatty() else "", Path.cwd()))
