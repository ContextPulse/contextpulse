# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jerard Ventures LLC
"""condense_events shapes the probe's input before it reaches the extraction model.

Action cp-extraction-input-chrome-not-content (2026-10-24). Measured on the eight
6-hour windows before 2026-10-28 09:30Z: 59.0% of prompt lines carried no text
(screen_capture, click, scroll, typing counts), and only 14.4% of OCR characters
survived the 600-char cap, which kept the tab strip and URL bar of every frame and
cut the content. The model was reading chrome, not the person's day.

condense_events:
- folds the textless events of one app/window run into ONE summary line, kept at
  the position of the first of them, so session timing survives;
- sends each OCR frame's NEW lines only (lines not in the previous frame of the
  same app and window), so the cap spends itself on content and an unchanged
  frame becomes a textless event that folds away;
- never touches the person's own words (voice, clipboard, agent, typed text).
"""

from __future__ import annotations

from contextpulse_core import probe


def _ev(eid, ts, modality, text="", app="Chrome", win="Docs", event_type=None, **extra):
    e = {
        "event_id": eid,
        "timestamp": ts,
        "modality": modality,
        "app_name": app,
        "window_title": win,
        "text": text,
    }
    if event_type is not None:
        e["event_type"] = event_type
    e.update(extra)
    return e


def test_empty_input_returns_empty():
    assert probe.condense_events([]) == []


def test_textless_run_folds_into_one_summary_line():
    evs = [
        _ev("c1", 100.0, "flow", event_type="click"),
        _ev("c2", 130.0, "flow", event_type="click"),
        _ev("s1", 160.0, "sight", event_type="screen_capture"),
        _ev("k1", 400.0, "keys", event_type="typing_burst"),
    ]
    out = probe.condense_events(evs)
    assert len(out) == 1
    [s] = out
    assert s["event_id"] == "c1"
    assert s["timestamp"] == 100.0
    assert s["modality"] == "activity"
    assert s["app_name"] == "Chrome" and s["window_title"] == "Docs"
    assert "4 events" in s["text"]
    assert "flow:click x2" in s["text"]
    assert "keys:typing_burst x1" in s["text"]
    assert "5m" in s["text"]  # 100 -> 400 s


def test_summary_sits_where_the_first_textless_event_was():
    evs = [
        _ev("o1", 100.0, "sight", "Meeting notes draft", event_type="ocr_result"),
        _ev("c1", 110.0, "flow", event_type="click"),
        _ev("v1", 120.0, "voice", "send it to Ananth", event_type="transcription"),
        _ev("c2", 130.0, "flow", event_type="click"),
    ]
    out = probe.condense_events(evs)
    assert [e["event_id"] for e in out] == ["o1", "c1", "v1"]
    assert out[1]["modality"] == "activity" and "2 events" in out[1]["text"]
    ts = [e["timestamp"] for e in out]
    assert ts == sorted(ts)


def test_window_change_starts_a_new_run():
    evs = [
        _ev("c1", 100.0, "flow", event_type="click", win="A"),
        _ev("c2", 110.0, "flow", event_type="click", win="B"),
    ]
    out = probe.condense_events(evs)
    assert [(e["window_title"], e["event_id"]) for e in out] == [("A", "c1"), ("B", "c2")]


def test_interleaved_windows_each_fold_once():
    # Captures alternate between two monitors' windows (measured: 1021 of 1635
    # key changes were WindowsTerminal <-> chrome), so a run must not break on
    # the other window's events.
    evs = []
    for i in range(6):
        evs.append(_ev(f"a{i}", 100.0 + 20 * i, "sight", event_type="screen_capture", win="A"))
        evs.append(_ev(f"b{i}", 110.0 + 20 * i, "sight", event_type="screen_capture", win="B"))
    out = probe.condense_events(evs)
    assert [(e["window_title"], e["event_id"]) for e in out] == [("A", "a0"), ("B", "b0")]
    assert all("6 events" in e["text"] for e in out)


def test_a_long_silence_in_a_window_starts_a_new_fold():
    evs = [
        _ev("c1", 100.0, "flow", event_type="click"),
        _ev("c2", 200.0, "flow", event_type="click"),
        _ev("c3", 200.0 + probe._FOLD_GAP_S + 1, "flow", event_type="click"),
    ]
    out = probe.condense_events(evs)
    assert [e["event_id"] for e in out] == ["c1", "c3"]
    assert "2 events" in out[0]["text"] and "1 events" in out[1]["text"]


def test_capture_then_ocr_pairs_fold_into_one_line():
    # Every frame is a textless screen_capture followed by its ocr_result; the
    # capture markers must fold to one line, not one per frame.
    evs = []
    for i in range(5):
        evs.append(_ev(f"s{i}", 100.0 + 30 * i, "sight", event_type="screen_capture"))
        evs.append(_ev(f"o{i}", 101.0 + 30 * i, "sight", f"page line {i}", event_type="ocr_result"))
    out = probe.condense_events(evs)
    assert [e["event_id"] for e in out] == ["s0", "o0", "o1", "o2", "o3", "o4"]
    assert "5 events" in out[0]["text"]


def test_ocr_frame_keeps_only_new_lines():
    chrome = "Inbox (3)\nhttps://docs.example.com/d/1\nFile Edit View"
    evs = [
        _ev("o1", 100.0, "sight", chrome + "\nQ3 plan: ship by Friday", event_type="ocr_result"),
        _ev("o2", 160.0, "sight", chrome + "\nQ3 plan: ship by Friday\nOwner: Dana", event_type="ocr_result"),
    ]
    out = probe.condense_events(evs)
    assert [e["event_id"] for e in out] == ["o1", "o2"]
    assert "Inbox (3)" in out[0]["text"]  # the first frame of a window is sent whole
    assert out[1]["text"] == "Owner: Dana"


def test_unchanged_ocr_frame_becomes_textless_and_folds():
    page = "Q3 plan: ship by Friday\nOwner: Dana"
    evs = [
        _ev("o1", 100.0, "sight", page, event_type="ocr_result"),
        _ev("o2", 160.0, "sight", page, event_type="ocr_result"),
        _ev("o3", 220.0, "sight", page, event_type="ocr_result"),
    ]
    out = probe.condense_events(evs)
    assert [e["event_id"] for e in out] == ["o1", "o2"]
    assert out[1]["modality"] == "activity"
    assert "2 events" in out[1]["text"] and "unchanged" in out[1]["text"]


def test_ocr_delta_is_tracked_per_window():
    evs = [
        _ev("a1", 100.0, "sight", "alpha page", win="A", event_type="ocr_result"),
        _ev("b1", 110.0, "sight", "beta page", win="B", event_type="ocr_result"),
        _ev("a2", 120.0, "sight", "alpha page\nalpha new", win="A", event_type="ocr_result"),
    ]
    out = probe.condense_events(evs)
    assert [e["text"] for e in out] == ["alpha page", "beta page", "alpha new"]


def test_the_persons_own_words_are_never_dropped_or_folded():
    said = "book the Tuesday flight"
    evs = [
        _ev("v1", 100.0, "voice", said),
        _ev("v2", 110.0, "voice", said),
        _ev("p1", 120.0, "clipboard", "ref 4471"),
        _ev("p2", 130.0, "clipboard", "ref 4471"),
        _ev("t1", 140.0, "agent", "ship it", max_chars=4000),
        _ev("t2", 150.0, "agent", "ship it", max_chars=4000),
        _ev("k1", 160.0, "touch", "draft reply", event_type="burst"),
        _ev("k2", 170.0, "touch", "draft reply", event_type="burst"),
    ]
    out = probe.condense_events(evs)
    assert [e["event_id"] for e in out] == ["v1", "v2", "p1", "p2", "t1", "t2", "k1", "k2"]
    assert [e["text"] for e in out] == [e["text"] for e in evs]
    assert out[4]["max_chars"] == 4000  # transcript budget survives


def test_missing_event_type_still_folds_by_modality():
    out = probe.condense_events([_ev("c1", 100.0, "flow"), _ev("c2", 101.0, "flow")])
    assert len(out) == 1 and "flow x2" in out[0]["text"]


def test_input_list_is_not_mutated():
    evs = [_ev("o1", 100.0, "sight", "x\ny"), _ev("o2", 110.0, "sight", "x\ny\nz")]
    before = [dict(e) for e in evs]
    probe.condense_events(evs)
    assert evs == before


def test_read_recent_events_carries_event_type(tmp_path):
    import json
    import sqlite3

    c = sqlite3.connect(str(tmp_path / "a.db"))
    c.execute(
        "CREATE TABLE events (event_id TEXT, timestamp REAL, modality TEXT, event_type TEXT,"
        " app_name TEXT, window_title TEXT, payload TEXT)"
    )
    c.execute(
        "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
        ("e1", 1.0, "flow", "click", "A", "w", json.dumps({"x": 1})),
    )
    c.commit()
    c.row_factory = sqlite3.Row
    [e] = probe.read_recent_events(c, since_ts=0.0)
    assert e["event_type"] == "click"


def test_header_does_not_claim_typing_events_carry_words():
    h = probe._PROMPT_HEADER
    assert "touch =\ntyped text" not in h and "touch = typed text" not in h
    assert "keys" in h and "flow" in h
    assert "activity" in h
    assert "never the words" in h.lower() or "not the words" in h.lower()
