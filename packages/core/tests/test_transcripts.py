# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""Claude Code transcripts as a fact-store source (cp-transcript-event-source).

Three properties, each tested separately:

FILTER    only what a person typed or dictated reaches the store: no tool
          results, meta records, command echoes, compaction summaries,
          interrupted markers, sidechains, subagent files, headless or
          scheduled runs, System32/Temp working folders, the distiller's own
          runs, or anything from an excluded estate.
REDACT    a planted credential, token, one-time code, card or account number
          never survives into what the reader hands out.
ONCE      each prompt is ingested exactly once, including a prompt that a
          forked session copied into a second file.

Every secret below is SYNTHETIC.
"""

from __future__ import annotations

import builtins
import json
import time
from pathlib import Path

import pytest
from contextpulse_core import probe, transcripts

NOW = time.time()


@pytest.fixture(autouse=True)
def _keep_short_fixture_prompts(monkeypatch):
    """Fixtures use short texts; the length floor has its own test below."""
    monkeypatch.setattr(transcripts, "_MIN_PROMPT_CHARS", 1)


@pytest.fixture(autouse=True)
def _excluded_estate(monkeypatch, tmp_path):
    """One synthetic excluded estate, and never the machine's own excludes file."""
    monkeypatch.setenv(transcripts.EXCLUDES_ENV, "estatex")
    monkeypatch.setattr(transcripts, "default_excludes_file", lambda: tmp_path / "absent.txt")


def test_short_replies_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(transcripts, "_MIN_PROMPT_CHARS", 15)
    _write(
        tmp_path,
        "C--Users-dev-Projects",
        "a",
        [
            _user("Yes", uuid="u1"),
            _user("Do it", uuid="u2", offset=1),
            _user("Use the Acme card.", uuid="u3", offset=2),
        ],
    )
    assert _texts(tmp_path) == ["Use the Acme card."]


def _iso(offset_s: float = 0.0) -> str:
    t = NOW - 3600 + offset_s
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def _user(
    text,
    *,
    uuid,
    cwd=r"C:\Users\dev\Projects\ContextPulse",
    entry="cli",
    src="typed",
    origin="human",
    offset=0.0,
    **extra,
):
    rec = {
        "type": "user",
        "uuid": uuid,
        "sessionId": "s",
        "timestamp": _iso(offset),
        "cwd": cwd,
        "entrypoint": entry,
        "isSidechain": False,
        "message": {"role": "user", "content": text},
    }
    if src is not None:
        rec["promptSource"] = src
    if origin is not None:
        rec["origin"] = {"kind": origin}
    rec.update(extra)
    return rec


def _write(root: Path, project: str, name: str, records: list[dict]) -> Path:
    d = root / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.jsonl"
    p.write_text(
        "\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n", encoding="utf-8"
    )
    return p


def _texts(root: Path, **kw) -> list[str]:
    return [p.text for p in transcripts.iter_prompts(root, since_ts=NOW - 86400, **kw)]


# ── FILTER ──────────────────────────────────────────────────────────


class TestFilter:
    def test_typed_and_queued_prompts_are_kept(self, tmp_path):
        _write(
            tmp_path,
            "C--Users-dev-Projects",
            "a",
            [
                _user("You can read my work mail through IMAP.", uuid="u1"),
                _user("And the JV inbox too.", uuid="u2", src="queued", offset=5),
            ],
        )
        assert _texts(tmp_path) == [
            "You can read my work mail through IMAP.",
            "And the JV inbox too.",
        ]

    def test_desktop_and_phone_prompts_are_kept(self, tmp_path):
        _write(
            tmp_path,
            "C--Users-dev-Projects",
            "a",
            [
                _user("Book the Marlow in Springfield.", uuid="u1", entry="claude-desktop", src="sdk"),
                _user(
                    "Use the Acme card for it.",
                    uuid="u2",
                    entry="claude-desktop",
                    src="sdk",
                    offset=5,
                ),
            ],
        )
        assert len(_texts(tmp_path)) == 2

    def test_list_content_text_blocks_join_and_images_alone_drop(self, tmp_path):
        _write(
            tmp_path,
            "C--Users-dev-Projects",
            "a",
            [
                _user(
                    [
                        {"type": "text", "text": "Look at this error."},
                        {"type": "image", "source": {}},
                    ],
                    uuid="u1",
                ),
                _user([{"type": "image", "source": {}}], uuid="u2", offset=5),
            ],
        )
        assert _texts(tmp_path) == ["Look at this error."]

    @pytest.mark.parametrize(
        "record",
        [
            _user([{"type": "tool_result", "content": "zqtoolresult"}], uuid="x"),
            _user("zqmeta caveat", uuid="x", isMeta=True),
            _user("<command-name>/model</command-name> zqcommand", uuid="x"),
            _user(
                "This session is being continued from a previous conversation zqcompact",
                uuid="x",
                isCompactSummary=True,
            ),
            _user("[Request interrupted by user] zqinterrupt", uuid="x"),
            _user("zqsidechain", uuid="x", isSidechain=True),
            _user("zqnotification", uuid="x", src="system", origin="task-notification"),
            _user("zqnoorigin", uuid="x", src=None, origin=None),
        ],
        ids=[
            "tool_result",
            "meta",
            "command",
            "compaction",
            "interrupted",
            "sidechain",
            "task_notification",
            "no_origin",
        ],
    )
    def test_non_human_records_are_dropped(self, tmp_path, record):
        _write(
            tmp_path,
            "C--Users-dev-Projects",
            "a",
            [
                record,
                _user("keep me", uuid="k1", offset=5),
            ],
        )
        assert _texts(tmp_path) == ["keep me"]

    def test_subagent_transcripts_in_subfolders_are_not_read(self, tmp_path):
        _write(tmp_path, "C--Users-dev-Projects", "a", [_user("top", uuid="u1")])
        _write(
            tmp_path / "C--Users-dev-Projects" / "a",
            "subagents",
            "agent-1",
            [_user("zqsubagent", uuid="u2")],
        )
        assert _texts(tmp_path) == ["top"]

    @pytest.mark.parametrize(
        "cwd",
        [
            r"C:\Windows\System32",
            r"C:\Users\dev\AppData\Local\Temp\cp-probe-abc123",
            r"C:\Users\dev\AppData\Local\Temp\claude\work",
            r"C:\Users\pat\Projects\EstateX",
            r"C:\Users\pat\.claude-estatex\runs\r1",
        ],
    )
    def test_excluded_working_folders(self, tmp_path, cwd):
        _write(
            tmp_path, "C--Users-dev-Projects-x", "a", [_user("zqexcluded", uuid="u1", cwd=cwd)]
        )
        assert _texts(tmp_path) == []

    def test_excluded_estate_project_folders_are_never_opened(self, tmp_path, monkeypatch):
        """An excluded estate's folders are not filtered after reading -- never opened.
        A real projects folder does hold such folders."""
        _write(
            tmp_path,
            "C--Users-pat--claude-estatex-runs-r1",
            "a",
            [_user("zqaf", uuid="u1", cwd=r"C:\Users\dev\Projects")],
        )
        _write(
            tmp_path,
            "C--Users-pat-Projects-EstateX",
            "b",
            [_user("zqaf2", uuid="u2", cwd=r"C:\Users\dev\Projects")],
        )
        _write(tmp_path, "C--Users-dev-Projects", "c", [_user("ok", uuid="u3")])
        opened: list[str] = []
        real_open = builtins.open

        def spy(file, *a, **k):
            opened.append(str(file))
            return real_open(file, *a, **k)

        monkeypatch.setattr(builtins, "open", spy)
        assert _texts(tmp_path) == ["ok"]
        rel = [o[len(str(tmp_path)) :] for o in opened if o.startswith(str(tmp_path))]
        assert rel, "the spy saw no reads at all"
        assert not [r for r in rel if "estatex" in r.lower()], rel

    def test_single_prompt_headless_run_is_dropped(self, tmp_path):
        _write(
            tmp_path,
            "C--Users-dev-Projects",
            "a",
            [_user("zqheadless do the thing", uuid="u1", entry="sdk-cli", src="sdk")],
        )
        assert _texts(tmp_path) == []

    @pytest.mark.parametrize(
        "first",
        [
            "You are running as a scheduled director cycle. zqbrief",
            "# Task: refresh the wiki\nzqbrief",
            "This is an automated run. zqbrief",
        ],
    )
    def test_scheduled_or_headless_brief_session_is_dropped(self, tmp_path, first):
        _write(
            tmp_path,
            "C--Users-dev-Projects",
            "a",
            [
                _user(first, uuid="u1"),
                _user("zqfollow", uuid="u2", offset=5),
            ],
        )
        assert _texts(tmp_path) == []

    def test_pasted_handoff_brief_is_dropped_but_session_kept(self, tmp_path):
        brief = "# Handoff brief\n" + ("state line\n" * 200)
        _write(
            tmp_path,
            "C--Users-dev-Projects",
            "a",
            [
                _user("resume from the brief", uuid="u0"),
                _user(brief, uuid="u1", offset=5),
                _user("my Southwest number is on the card", uuid="u2", offset=9),
            ],
        )
        assert _texts(tmp_path) == ["resume from the brief", "my Southwest number is on the card"]

    def test_files_older_than_since_are_skipped_by_record_time(self, tmp_path):
        _write(
            tmp_path,
            "C--Users-dev-Projects",
            "a",
            [
                _user("zqold", uuid="u1", offset=-10 * 86400),
                _user("new", uuid="u2"),
            ],
        )
        assert _texts(tmp_path) == ["new"]

    def test_prompt_metadata(self, tmp_path):
        _write(
            tmp_path,
            "C--Users-dev-Projects-ContextPulse",
            "sess1",
            [
                _user("hello there", uuid="abcd1234-ef00-0000-0000-000000000000"),
            ],
        )
        [p] = list(transcripts.iter_prompts(tmp_path, since_ts=NOW - 86400))
        assert p.key == "abcd1234-ef00-0000-0000-000000000000"
        assert p.project == "ContextPulse"
        assert p.session_id == "sess1"
        assert abs(p.ts - (NOW - 3600)) < 2

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            list(transcripts.iter_prompts(tmp_path / "nope", since_ts=0))


# ── REDACT ──────────────────────────────────────────────────────────

PLANTED = [
    ("anthropic", "here is the key sk-ant-api03-zqPLANTEDabcdefghij0123456789", "zqPLANTED"),
    ("github", "token ghp_zqplanted0123456789abcdefghijklmnopqr", "zqplanted0123"),
    ("aws", "AKIAZQPLANTED0123456 is the access key", "AKIAZQPLANTED"),
    ("jwt", "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.zqSIGNATUREabc123", "zqSIGNATURE"),
    ("password_prose", "my password is Tr0ub4dor&3zq ok", "Tr0ub4dor"),
    ("password_colon", "Password: hunter2zqq", "hunter2zqq"),
    ("otp_is", "the code is 269304, enter it now", "269304"),
    ("otp_colon", "OTP: 482913", "482913"),
    ("one_time", "a one-time code 771204 was used", "771204"),
    ("passcode_list", 'Passcodes he attempted ("0214," "021480") failed', "021480"),
    ("passcode_list_first", 'Passcodes he attempted ("0214," "021480") failed', "0214"),
    ("pin", "my PIN is 8642", "8642"),
    ("verification", "verification code: 559012", "559012"),
    ("visa", "card 4111 1111 1111 1111 exp 12/29", "4111 1111 1111 1111"),
    ("visa_nospace", "card 4111111111111111", "4111111111111111"),
    ("amex", "amex 3782 822463 10005", "822463"),
    ("routing", "routing number 021000021 for the bank", "021000021"),
    ("account", "account number 123456789012", "123456789012"),
    ("acct_hash", "acct # 9876543210", "9876543210"),
    ("ssn", "SSN 123-45-6789", "123-45-6789"),
    ("generic_token", "set it to Zq7pLm2Xr9TbQw4Vn8Ks6Hd3Fj1Gy5Ac0", "Zq7pLm2Xr9TbQw4Vn8Ks"),
]


class TestRedaction:
    @pytest.mark.parametrize("label,text,needle", PLANTED, ids=[p[0] for p in PLANTED])
    def test_planted_secret_does_not_survive_redactor(self, label, text, needle):
        out = transcripts.redact_transcript_text(text)
        assert needle not in out, out
        assert "[REDACTED" in out

    @pytest.mark.parametrize("label,text,needle", PLANTED, ids=[p[0] for p in PLANTED])
    def test_planted_secret_does_not_survive_reader_or_prompt(self, tmp_path, label, text, needle):
        _write(tmp_path, "C--Users-dev-Projects", "a", [_user(text, uuid="u1")])
        [p] = list(transcripts.iter_prompts(tmp_path, since_ts=NOW - 86400))
        assert needle not in p.text
        prompt = probe.build_extraction_prompt(transcripts.to_events([p]))
        assert needle not in prompt

    @pytest.mark.parametrize(
        "text",
        [
            "Why can't you use the password manager?",
            "the build failed with exit code 1",
            "we shipped it on 2026-10-24 at 14:05",
            "the card ending 4321 is the Acme one",
            "commit 6567d18 and session 6ca9e2b7-577e-4992-aab6-6289549e4a09",
            "action cp-consolidator-silent-step-failures is closed",
            "HTTP status code 403 from the API",
            "my Southwest Rapid Rewards number is on file",
            "I would think that the password is probably already in Keyvault",
        ],
    )
    def test_ordinary_text_survives(self, text):
        assert transcripts.redact_transcript_text(text) == text

    def test_redaction_is_idempotent(self):
        once = transcripts.redact_transcript_text(" ".join(p[1] for p in PLANTED))
        assert transcripts.redact_transcript_text(once) == once

    def test_secret_straddling_truncation_is_redacted_first(self, tmp_path):
        text = ("x " * 990) + "sk-ant-api03-zqTRUNCATEDabcdefghij0123456789"
        _write(tmp_path, "C--Users-dev-Projects", "a", [_user(text, uuid="u1")])
        [p] = list(transcripts.iter_prompts(tmp_path, since_ts=NOW - 86400))
        prompt = probe.build_extraction_prompt(transcripts.to_events([p], max_chars=1990))
        assert "zqTRUNC" not in prompt


# ── events and ONCE ─────────────────────────────────────────────────


class TestEventsAndLedger:
    def test_to_events_shape(self, tmp_path):
        _write(
            tmp_path,
            "C--Users-dev-Projects-Invoicer",
            "a",
            [
                _user(
                    "ship it",
                    uuid="0e45974d-74f0-4a93-87aa-340c26d5ecce",
                    cwd=r"C:\Users\dev\Projects\Invoicer",
                )
            ],
        )
        [e] = transcripts.to_events(list(transcripts.iter_prompts(tmp_path, since_ts=0)))
        assert e["event_id"] == "cc-0e45974d"
        assert e["modality"] == "agent"
        assert e["app_name"] == "Claude Code"
        assert e["window_title"] == "Invoicer"
        assert e["text"] == "ship it"

    def test_long_prompt_keeps_more_than_screen_cap(self, tmp_path):
        text = "word " * 600  # 3000 chars
        _write(tmp_path, "C--Users-dev-Projects", "a", [_user(text, uuid="u1")])
        events = transcripts.to_events(
            list(transcripts.iter_prompts(tmp_path, since_ts=0)), max_chars=2000
        )
        line = probe.build_extraction_prompt(events).rsplit(":: ", 1)[1]
        assert 1990 <= len(line.split("\n")[0]) <= 2010

    def test_forked_copy_is_ingested_once(self, tmp_path):
        rec = _user("fact stated once", uuid="dup-1")
        _write(tmp_path, "C--Users-dev-Projects", "orig", [rec])
        _write(tmp_path, "C--Users-dev-Projects", "fork", [rec])
        conn = probe.connect_probe(tmp_path / "probe.db")
        batch = transcripts.select_unseen(
            conn, transcripts.iter_prompts(tmp_path, since_ts=0), max_total_chars=10_000
        )
        assert [p.text for p in batch] == ["fact stated once"]

    def test_mark_then_select_returns_nothing(self, tmp_path):
        _write(
            tmp_path,
            "C--Users-dev-Projects",
            "a",
            [_user("one", uuid="u1"), _user("two", uuid="u2", offset=5)],
        )
        conn = probe.connect_probe(tmp_path / "probe.db")
        first = transcripts.select_unseen(
            conn, transcripts.iter_prompts(tmp_path, since_ts=0), max_total_chars=10_000
        )
        assert len(first) == 2
        transcripts.mark_ingested(conn, first)
        transcripts.mark_ingested(conn, first)  # idempotent
        again = transcripts.select_unseen(
            conn, transcripts.iter_prompts(tmp_path, since_ts=0), max_total_chars=10_000
        )
        assert again == []
        assert conn.execute("SELECT COUNT(*) FROM transcript_ingest").fetchone()[0] == 2

    def test_budget_takes_oldest_first_and_leaves_rest(self, tmp_path):
        _write(
            tmp_path,
            "C--Users-dev-Projects",
            "a",
            [
                _user("c" * 100, uuid="u3", offset=20),
                _user("a" * 100, uuid="u1", offset=0),
                _user("b" * 100, uuid="u2", offset=10),
            ],
        )
        conn = probe.connect_probe(tmp_path / "probe.db")
        batch = transcripts.select_unseen(
            conn,
            transcripts.iter_prompts(tmp_path, since_ts=0),
            max_total_chars=250,
            max_chars=2000,
        )
        assert [p.key for p in batch] == ["u1", "u2"]
        transcripts.mark_ingested(conn, batch)
        rest = transcripts.select_unseen(
            conn,
            transcripts.iter_prompts(tmp_path, since_ts=0),
            max_total_chars=250,
            max_chars=2000,
        )
        assert [p.key for p in rest] == ["u3"]

    def test_one_oversized_prompt_still_fits_after_truncation(self, tmp_path):
        _write(tmp_path, "C--Users-dev-Projects", "a", [_user("z" * 9000, uuid="u1")])
        conn = probe.connect_probe(tmp_path / "probe.db")
        batch = transcripts.select_unseen(
            conn,
            transcripts.iter_prompts(tmp_path, since_ts=0),
            max_total_chars=2500,
            max_chars=2000,
        )
        assert [p.key for p in batch] == ["u1"]


def test_excluded_estate_root_is_refused():
    with pytest.raises(ValueError, match="estatex"):
        transcripts.check_personal_root(Path("C:/Users/pat/.claude-estatex/projects"))
    transcripts.check_personal_root(Path("C:/Users/pat/.claude/projects"))


# -- CONFIGURED EXCLUDES ------------------------------------------------


def test_no_estate_name_is_written_in_the_module():
    """Estate names are private to whoever runs the store; the code is public."""
    assert not hasattr(transcripts, "DEFAULT_EXCLUDES")
    assert transcripts.configured_excludes({}, Path("absent-file.txt")) == ()


def test_excludes_merge_env_and_file(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("# private estates\nEstateY  # trailing note\n\nestatex\n", encoding="utf-8")
    env = {transcripts.EXCLUDES_ENV: " EstateX, ,estateZ "}
    assert transcripts.configured_excludes(env, f) == ("estatex", "estatez", "estatey")


def test_unreadable_excludes_file_fails_loud(tmp_path):
    """Reading nothing would ingest the very estates the file names."""
    d = tmp_path / "is-a-dir.txt"
    d.mkdir()
    with pytest.raises(OSError):
        transcripts.configured_excludes({}, d)


def test_file_excludes_apply_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(transcripts.EXCLUDES_ENV)
    f = tmp_path / "excl.txt"
    f.write_text("estatew\n", encoding="utf-8")
    monkeypatch.setattr(transcripts, "default_excludes_file", lambda: f)
    _write(tmp_path / "root", "C--Users-pat-Projects-EstateW", "a", [_user("zqw", uuid="u1")])
    _write(tmp_path / "root", "C--Users-pat-Projects", "b", [_user("ok", uuid="u2")])
    assert _texts(tmp_path / "root") == ["ok"]
