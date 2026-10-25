# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Transcript prompts as a second distiller source (cp-transcript-event-source).

Drives scripts/probe_consolidator.py main() against a real on-disk activity.db,
probe.db and a fixture transcripts root, mocking only call_claude. The
properties: off unless asked for; each prompt reaches the model once; a failed
run marks nothing; a fact quoting a planted secret is stored redacted.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from contextpulse_core import probe, transcripts

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "probe_consolidator.py"
NOW = time.time()


def _load():
    spec = importlib.util.spec_from_file_location("probe_consolidator_tx", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cons():
    return _load()


def _activity(tmp_path: Path, n: int = 1) -> Path:
    p = tmp_path / "activity.db"
    c = sqlite3.connect(str(p))
    c.execute(
        "CREATE TABLE events (event_id TEXT, timestamp REAL, modality TEXT, "
        "event_type TEXT, app_name TEXT, window_title TEXT, payload TEXT)"
    )
    for i in range(n):
        c.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
            (
                f"e{i}",
                NOW - 60,
                "sight",
                "ocr",
                "Chrome",
                "Inbox",
                json.dumps({"ocr_text": "zqscreenline"}),
            ),
        )
    c.commit()
    c.close()
    return p


def _root(tmp_path: Path, prompts: list[tuple[str, str]]) -> Path:
    root = tmp_path / "projects"
    d = root / "C--Users-david-Projects"
    d.mkdir(parents=True)
    lines = []
    for i, (uuid, text) in enumerate(prompts):
        t = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(NOW - 600 + i)) + ".000Z"
        lines.append(
            json.dumps(
                {
                    "type": "user",
                    "uuid": uuid,
                    "timestamp": t,
                    "cwd": r"C:\Users\david\Projects",
                    "entrypoint": "cli",
                    "promptSource": "typed",
                    "origin": {"kind": "human"},
                    "isSidechain": False,
                    "message": {"role": "user", "content": text},
                },
                separators=(",", ":"),
            )
        )
    (d / "sess.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def _run(cons, act, pdb, *extra):
    return cons.main(["--hours", "1", "--activity-db", str(act), "--probe-db", str(pdb), *extra])


def _tx(root):
    return ["--transcripts", "--transcripts-root", str(root)]


FACT = json.dumps(
    [
        {
            "entity": "Apple Mail",
            "fact": "David's Apple mail is readable over IMAP.",
            "valid_from": NOW - 600,
            "source_event_ids": ["cc-aaaa1111"],
            "confidence": 0.9,
        }
    ]
)


def _ledger(pdb):
    c = probe.connect_probe(pdb)
    transcripts._ensure_ledger(c)
    n = c.execute("SELECT COUNT(*) FROM transcript_ingest").fetchone()[0]
    c.close()
    return n


def test_off_by_default_never_reads_transcripts(cons, tmp_path):
    act, pdb = _activity(tmp_path), tmp_path / "probe.db"
    with (
        patch.object(transcripts, "iter_prompts", side_effect=AssertionError("read")),
        patch.object(cons, "call_claude", return_value=("[]", 20.0)) as cc,
    ):
        assert _run(cons, act, pdb) == 0
    assert "agent app=" not in cc.call_args.args[0]


def test_prompt_reaches_model_once_and_is_marked(cons, tmp_path):
    act, pdb = _activity(tmp_path), tmp_path / "probe.db"
    root = _root(tmp_path, [("aaaa1111-0000", "You can read my Apple mail over IMAP.")])
    with patch.object(cons, "call_claude", return_value=(FACT, 20.0)) as cc:
        assert _run(cons, act, pdb, *_tx(root)) == 0
    sent = cc.call_args.args[0]
    assert "[cc-aaaa1111]" in sent and "agent app='Claude Code'" in sent
    assert "You can read my Apple mail over IMAP." in sent
    assert "zqscreenline" in sent  # screen events still included
    assert _ledger(pdb) == 1
    with patch.object(cons, "call_claude", return_value=("[]", 20.0)) as cc2:
        assert _run(cons, act, pdb, *_tx(root)) == 0
    assert "cc-aaaa1111" not in cc2.call_args.args[0]


def test_fault_marks_nothing_and_next_run_resends(cons, tmp_path):
    act, pdb = _activity(tmp_path), tmp_path / "probe.db"
    root = _root(tmp_path, [("bbbb2222-0000", "I already used the Hyatt offer.")])
    with patch.object(cons, "call_claude", return_value=("usage limit reached", 3.0)):
        assert _run(cons, act, pdb, *_tx(root)) == 1
    assert _ledger(pdb) == 0
    with patch.object(cons, "call_claude", return_value=("[]", 20.0)) as cc:
        assert _run(cons, act, pdb, *_tx(root)) == 0
    assert "cc-bbbb2222" in cc.call_args.args[0]
    assert _ledger(pdb) == 1


def test_fast_empty_fault_marks_nothing(cons, tmp_path):
    act, pdb = _activity(tmp_path), tmp_path / "probe.db"
    root = _root(tmp_path, [("cccc3333-0000", "Use the Premier card for travel.")])
    with patch.object(cons, "call_claude", return_value=("[]", 1.0)):
        rc = _run(cons, act, pdb, *_tx(root), "--min-prompt-chars", "0")
    assert rc == 1
    assert _ledger(pdb) == 0


def test_cli_error_marks_nothing(cons, tmp_path):
    act, pdb = _activity(tmp_path), tmp_path / "probe.db"
    root = _root(tmp_path, [("dddd4444-0000", "The Delta card is the travel card.")])
    with patch.object(cons, "call_claude", side_effect=RuntimeError("boom")):
        assert _run(cons, act, pdb, *_tx(root)) == 1
    assert _ledger(pdb) == 0


def test_transcripts_only_skips_screen(cons, tmp_path):
    act, pdb = _activity(tmp_path), tmp_path / "probe.db"
    root = _root(tmp_path, [("eeee5555-0000", "My Southwest number is on the card.")])
    with patch.object(cons, "call_claude", return_value=("[]", 20.0)) as cc:
        assert _run(cons, act, pdb, *_tx(root), "--transcripts-only") == 0
    sent = cc.call_args.args[0]
    assert "zqscreenline" not in sent and "cc-eeee5555" in sent


def test_transcripts_only_with_nothing_new_is_a_quiet_run(cons, tmp_path):
    act, pdb = _activity(tmp_path), tmp_path / "probe.db"
    root = _root(tmp_path, [])
    with patch.object(cons, "call_claude", side_effect=AssertionError("called")):
        assert _run(cons, act, pdb, *_tx(root), "--transcripts-only") == 0


def test_screen_empty_but_transcripts_present_still_runs(cons, tmp_path):
    act, pdb = _activity(tmp_path, n=0), tmp_path / "probe.db"
    root = _root(tmp_path, [("ffff6666-0000", "The CLEAR renewal is due in March.")])
    with patch.object(cons, "call_claude", return_value=("[]", 20.0)) as cc:
        assert _run(cons, act, pdb, *_tx(root)) == 0
    assert "cc-ffff6666" in cc.call_args.args[0]


def test_budget_drains_over_runs(cons, tmp_path):
    act, pdb = _activity(tmp_path), tmp_path / "probe.db"
    root = _root(tmp_path, [(f"{i:04d}aaaa-0000", "x" * 500) for i in range(5)])
    for expected_total in (2, 4, 5):
        with patch.object(cons, "call_claude", return_value=("[]", 20.0)):
            assert _run(cons, act, pdb, *_tx(root), "--transcripts-max-chars", "1000") == 0
        assert _ledger(pdb) == expected_total


def test_activefounder_root_refused(cons, tmp_path):
    act, pdb = _activity(tmp_path), tmp_path / "probe.db"
    bad = tmp_path / ".claude-activefounder" / "projects"
    bad.mkdir(parents=True)
    with patch.object(cons, "call_claude", side_effect=AssertionError("called")):
        with pytest.raises(SystemExit) as ei:
            _run(cons, act, pdb, *_tx(bad))
    assert ei.value.code == 2


def test_missing_root_fails_loud(cons, tmp_path):
    act, pdb = _activity(tmp_path), tmp_path / "probe.db"
    with patch.object(cons, "call_claude", side_effect=AssertionError("called")):
        assert _run(cons, act, pdb, *_tx(tmp_path / "nope")) == 1
    c = probe.connect_probe(pdb)
    [(err,)] = c.execute("SELECT error FROM probe_runs").fetchall()
    c.close()
    assert "transcript root not found" in err


def test_fact_quoting_a_secret_is_stored_redacted(cons, tmp_path):
    act, pdb = _activity(tmp_path), tmp_path / "probe.db"
    root = _root(tmp_path, [("abab7777-0000", "the OTP is 482913")])
    leaky = json.dumps(
        [
            {
                "entity": "Bank login",
                "fact": "Signed in with OTP 482913 and token "
                "sk-ant-api03-zqLEAKEDabcdefghij0123456789.",
                "valid_from": NOW,
                "confidence": 0.9,
            }
        ]
    )
    with patch.object(cons, "call_claude", return_value=(leaky, 20.0)) as cc:
        assert _run(cons, act, pdb, *_tx(root)) == 0
    assert "482913" not in cc.call_args.args[0]
    c = probe.connect_probe(pdb)
    [(fact,)] = c.execute("SELECT fact FROM facts").fetchall()
    c.close()
    assert "482913" not in fact and "zqLEAKED" not in fact
    assert "[REDACTED" in fact


def test_dry_run_marks_nothing(cons, tmp_path, capsys):
    act, pdb = _activity(tmp_path), tmp_path / "probe.db"
    root = _root(tmp_path, [("acac8888-0000", "a dry run line, long enough to keep")])
    assert _run(cons, act, pdb, *_tx(root), "--dry-run") == 0
    assert "cc-acac8888" in capsys.readouterr().out
    assert _ledger(pdb) == 0
