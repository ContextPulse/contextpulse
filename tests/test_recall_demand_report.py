"""Tests for scripts/recall_demand_report.py.

Record shapes mirror Claude Code transcript lines as checked on 2026-10-07:
user prompts are {"type":"user","message":{"role":"user","content":"<text>"},
"timestamp":"...Z"}; tool calls are assistant records whose message.content
holds {"type":"tool_use","name":...,"input":{...}} blocks.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "recall_demand_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("recall_demand_report_under_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


rdr = _load()


def _user(text: str, ts: str, **extra) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}, "timestamp": ts, **extra}


def _tool(name: str, ts: str, inp: dict | None = None) -> dict:
    block = {"type": "tool_use", "id": "toolu_x", "name": name, "input": inp or {}}
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [block]},
        "timestamp": ts,
    }


def _write(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_recall_shaped_prompt_counted_and_ordinary_prompt_not(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "proj" / "a.jsonl",
        [
            _user("we already discussed the renewal, why did you forget", "2026-10-04T01:00:00Z"),
            _user("please run the tests", "2026-10-04T01:01:00Z"),
        ],
    )
    t = rdr.tally_file(f, "2026-10-03T09:41")
    assert t.prompts == 2
    assert len(t.recall_prompts) == 1


def test_tool_results_meta_and_command_echoes_are_not_prompts() -> None:
    tool_result = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_x", "content": "we discussed"}
            ],
        },
    }
    assert rdr.human_prompts(tool_result) == []
    assert rdr.human_prompts(_user("last week we did X", "t", isMeta=True)) == []
    assert rdr.human_prompts(_user("<command-name>/clear</command-name>", "t")) == []


def test_records_before_the_window_are_ignored(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "p" / "b.jsonl",
        [
            _user("why did you forget", "2026-10-03T04:22:50Z"),
            _tool("mcp__contextpulse__facts_about", "2026-10-03T04:23:00Z"),
        ],
    )
    t = rdr.tally_file(f, "2026-10-03T09:41")
    assert (t.prompts, t.recall_prompts, t.recall_calls) == (0, [], 0)


def test_recall_calls_and_journal_queries_are_told_apart(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "p" / "c.jsonl",
        [
            _tool("mcp__contextpulse__facts_about", "2026-10-04T00:00:00Z", {"entity": "wiki"}),
            _tool("mcp__contextpulse__get_screenshot", "2026-10-04T00:00:01Z"),
            _tool(
                "Bash", "2026-10-04T00:00:02Z", {"command": "python query-journal.py --grep wiki"}
            ),
            _tool("Bash", "2026-10-04T00:00:03Z", {"command": "ls"}),
        ],
    )
    t = rdr.tally_file(f, "2026-10-03T09:41")
    assert t.recall_calls == 1
    assert t.tools.count(rdr.JOURNAL) == 1
    assert len([n for n in t.tools if n.startswith("mcp__")]) == 2


def test_interactive_only_drops_subagent_and_scheduled_transcripts(tmp_path: Path) -> None:
    keep = _write(tmp_path / "C--Users-x-projects" / "s.jsonl", [])
    _write(tmp_path / "C--Users-x-projects" / "s" / "subagents" / "agent-a.jsonl", [])
    _write(tmp_path / "C--Windows-System32" / "n.jsonl", [])
    assert rdr.find_transcripts([tmp_path], interactive_only=True) == [keep]
    assert len(rdr.find_transcripts([tmp_path], interactive_only=False)) == 3


def test_cli_refuses_an_empty_root(tmp_path: Path, capsys) -> None:
    assert rdr.main(["--root", str(tmp_path), "--after", "2026-10-03"]) == 2
    assert "REFUSING" in capsys.readouterr().err


def test_cli_reports_undefined_ratio_when_no_recall_prompts(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "p" / "d.jsonl", [_user("run the build", "2026-10-04T00:00:00Z")])
    assert rdr.main(["--root", str(tmp_path), "--after", "2026-10-03"]) == 0
    out = capsys.readouterr().out
    assert "recall-shaped prompts: 0" in out
    assert "routing ratio: undefined" in out


def test_cli_reports_the_ratio_when_demand_exists(tmp_path: Path, capsys) -> None:
    _write(
        tmp_path / "p" / "e.jsonl",
        [
            _user("did we already backfill the wiki?", "2026-10-04T00:00:00Z"),
            _tool(
                "Bash", "2026-10-04T00:00:01Z", {"command": "python query-journal.py --grep wiki"}
            ),
        ],
    )
    assert rdr.main(["--root", str(tmp_path), "--after", "2026-10-03"]) == 0
    out = capsys.readouterr().out
    assert "sessions reaching a recall tool: 0 of 1 (0%)" in out
    assert "journal queries: 1" in out


_DISTILLER_PROMPT = (
    "You are the nightly memory consolidator for a personal activity-capture system.\r\n"
    "Input: a chronological log... remember when the user said last week ..."
)


def test_distiller_transcripts_are_excluded_and_counted(tmp_path: Path, capsys) -> None:
    """The consolidator's own `claude -p` runs carry captured screen text, which is full of
    'last week' and 'we discussed'. Those are not David asking for recall."""
    _write(tmp_path / "C--Users-x-ContextPulse" / "d.jsonl", [_user(_DISTILLER_PROMPT, "2026-10-04T00:00:00Z")])
    _write(tmp_path / "p" / "h.jsonl", [_user("did we already backfill the wiki?", "2026-10-04T00:00:00Z")])
    assert rdr.main(["--root", str(tmp_path), "--after", "2026-10-03"]) == 0
    out = capsys.readouterr().out
    assert "distiller transcripts excluded: 1" in out
    assert "recall-shaped prompts: 1" in out
    assert "sessions reaching a recall tool: 0 of 1 (0%)" in out


def test_tally_flags_a_distiller_transcript(tmp_path: Path) -> None:
    f = _write(tmp_path / "p" / "d.jsonl", [_user(_DISTILLER_PROMPT, "2026-10-04T00:00:00Z")])
    assert rdr.tally_file(f, "2026-10-03").is_distiller is True
    g = _write(tmp_path / "p" / "g.jsonl", [_user("run the tests", "2026-10-04T00:00:00Z")])
    assert rdr.tally_file(g, "2026-10-03").is_distiller is False


def test_compaction_summaries_are_not_prompts() -> None:
    summary = _user(
        "This session is being continued from a previous conversation that ran out of context. "
        "Last week we discussed ...",
        "t",
        isCompactSummary=True,
    )
    assert rdr.human_prompts(summary) == []


def test_sessions_reached_counts_sessions_not_calls(tmp_path: Path, capsys) -> None:
    """Two recall calls in one session and none in another is 1 of 2 sessions, not 2/2."""
    _write(
        tmp_path / "p" / "a.jsonl",
        [
            _user("why did you forget the renewal", "2026-10-04T00:00:00Z"),
            _tool("mcp__contextpulse__facts_about", "2026-10-04T00:00:01Z"),
            _tool("mcp__contextpulse__context_at", "2026-10-04T00:00:02Z"),
        ],
    )
    _write(tmp_path / "p" / "b.jsonl", [_user("we already did this last week", "2026-10-04T00:00:00Z")])
    assert rdr.main(["--root", str(tmp_path), "--after", "2026-10-03"]) == 0
    out = capsys.readouterr().out
    assert "sessions reaching a recall tool: 1 of 2 (50%)" in out
    assert "recall calls (facts_about/context_at): 2" in out
