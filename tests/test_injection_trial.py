# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""The injection-trial instrument: use detection and the save refusal.

The report decides whether the trial passed, so its detectors get tests that
must be able to fail: echoing David's own words is not a use, and an injection
save without a fact id is refused.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look their module up while the class is built
    spec.loader.exec_module(mod)
    return mod


report = _load("injection_trial_report")
probe_save = _load("probe_save")
ri = report.ri

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
FACT = ri.Fact(7, "WezTerm", "Added a Shift+Enter keybinding because Alt+Enter was shadowed by split-pane", 0.0, 0.9)


def _index():
    filler = [ri.Fact(100 + i, f"T{i}", f"routine weekly planning note {i}", 0.0, 0.9) for i in range(30)]
    return ri.Index(filler + [FACT])


def _transcript(prompts, assistant):
    t = report.Transcript(Path("x.jsonl"))
    t.prompts = [(T0, p) for p in prompts]
    t.assistant = [(T0 + timedelta(seconds=30 + i), a) for i, a in enumerate(assistant)]
    return t


def test_a_cited_fact_is_a_use():
    t = _transcript(["fix wezterm"], ["Per cp#7 the bind was shadowed."])
    assert report.detect_uses(FACT, T0, t, _index())


def test_two_distinctive_words_are_a_use():
    t = _transcript(["fix wezterm"], ["The keybinding was shadowed by the split pane bind."])
    assert report.detect_uses(FACT, T0, t, _index())


def test_echoing_davids_own_words_is_not_a_use():
    t = _transcript(["the keybinding is shadowed again"], ["Looking at why the keybinding is shadowed."])
    assert not report.detect_uses(FACT, T0, t, _index())


def test_output_before_the_injection_is_not_a_use():
    t = _transcript(["fix wezterm"], ["The keybinding was shadowed."])
    assert not report.detect_uses(FACT, T0 + timedelta(hours=1), t, _index())


def test_another_facts_citation_is_not_a_use():
    t = _transcript(["fix wezterm"], ["Per cp#70 something else."])
    assert not report.detect_uses(FACT, T0, t, _index())


def test_inject_save_without_a_fact_id_is_refused(capsys):
    rc = probe_save.main(["--tool", "inject", "--query", "q", "--confirmed-novel"])
    assert rc == 2 and "--fact-id" in capsys.readouterr().err


def test_inject_save_without_confirmed_novel_is_refused():
    assert probe_save.main(["--tool", "inject", "--query", "q", "--fact-id", "7"]) == 2
