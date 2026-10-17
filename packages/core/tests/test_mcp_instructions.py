# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""The unified MCP server must tell clients when to use the recall tools.

Claude Code loads MCP tools deferred: a session sees only tool NAMES until it
searches for one, so a tool's docstring never reaches a session that has not
already decided to use it. Server instructions are the one MCP field Claude
Code puts in every session's system prompt. Without them, facts_about and
context_at were reached only via the using-contextpulse skill, and were called
twice in the 13 days after the 2026-09-19 Phase 0 restart (needs 20).

These tests read the instructions from the server's own initialization options,
which is the value the server sends back in its InitializeResult.
"""

import pytest
from contextpulse_core import mcp_unified

pytestmark = pytest.mark.unit


def _sent_instructions() -> str | None:
    app = mcp_unified.build_app("127.0.0.1", 8420)
    return app._mcp_server.create_initialization_options().instructions


def test_build_app_sends_non_empty_instructions():
    sent = _sent_instructions()
    assert sent, "unified server sends no instructions; recall tools stay invisible"


@pytest.mark.parametrize("tool", ["facts_about", "context_at"])
def test_instructions_name_each_recall_tool(tool):
    assert tool in _sent_instructions()


def test_instructions_say_how_to_load_deferred_tools():
    # A session that sees the advice but not the tool must know how to load it.
    assert "ToolSearch" in _sent_instructions()


def test_instructions_put_recall_before_journal_and_memory_search():
    # Measured 2026-10-03: in the 24 h after the first instructions shipped, three
    # interactive sessions received them and none called facts_about. When David
    # wrote "you were doing this for me like a week ago", the session went straight
    # to query-journal.py, the estate's documented recall route. The instruction
    # has to name that competing route and say which comes first.
    sent = _sent_instructions().lower()
    assert "journal" in sent
    assert sent.index("facts_about") < sent.index("journal")


@pytest.mark.parametrize("cue", ["we discussed", "last week", "why did you forget"])
def test_instructions_cover_references_to_earlier_events(cue):
    # The missed questions referred to an earlier event, not a name:
    # "we already discussed the clear renewal...why did you forget",
    # "you were doing this for me like a week ago". Name-only wording missed them.
    assert cue in _sent_instructions().lower()


def test_instructions_stay_short():
    # Paid on every session's system prompt; keep it to a short paragraph.
    assert len(_sent_instructions()) <= 900


def test_build_app_keeps_stateless_http_and_transport_security():
    app = mcp_unified.build_app("127.0.0.1", 8420)
    assert app.settings.stateless_http is True
    assert app.settings.port == 8420
    assert app.settings.transport_security is not None
