# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Glama.ai registry stub for ContextPulse.

ContextPulse is a local-only desktop daemon that captures screen, voice, keyboard,
mouse, and clipboard activity on the user's own machine. It cannot function inside
a remote container: there is no screen to capture, no microphone, no keyboard.

This stub satisfies Glama's MCP handshake so the server can be listed in the
registry for discovery. Tool definitions describe the real local-daemon behavior
(per Glama's Tool Definition Quality Score rubric); each stub returns an
installation pointer at runtime. Users install ContextPulse locally and point
their MCP client at localhost:8420/mcp to use the real tools.

Install: https://github.com/ContextPulse/contextpulse
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

GITHUB_URL = "https://github.com/ContextPulse/contextpulse"
SITE_URL = "https://contextpulse.ai"
LOCAL_ENDPOINT = "http://localhost:8420/mcp"

_LOCAL_ONLY_MSG = (
    f"ContextPulse runs only on the user's local machine. Install from {GITHUB_URL} "
    f"and connect your MCP client to {LOCAL_ENDPOINT}. This Glama stub exists for "
    f"registry discovery and tool catalog browsing only — call the real tool against "
    f"the local daemon for actual data."
)

mcp_app = FastMCP("ContextPulse")


# ---------------------------------------------------------------------------
# About / metadata
# ---------------------------------------------------------------------------

@mcp_app.tool()
def about() -> str:
    """Return a summary of ContextPulse: what it captures, how to install, and where to connect.

    Returns a multi-line string describing the daemon, its data sources, the local
    MCP endpoint, and primary documentation URLs.

    USE WHEN: an agent needs to learn what ContextPulse is or where to find docs
    before deciding to use other ContextPulse tools.
    NOT FOR: fetching live screen/voice/activity data — use get_screenshot,
    get_screen_text, get_recent_voice, or get_activity_summary for that.
    ALTERNATIVES: open GITHUB_URL or SITE_URL directly for human-readable docs.

    BEHAVIOR: pure read of static metadata. No side effects, no auth, no rate
    limits. Safe to call from any agent at any time.
    """
    return (
        "ContextPulse is a local desktop daemon that captures screen (with OCR), "
        "voice (Whisper), keyboard/mouse activity, and clipboard, then exposes the "
        "data to AI agents over MCP. All processing is local; no cloud, no telemetry. "
        f"Install: {GITHUB_URL}  Site: {SITE_URL}  Local MCP endpoint: {LOCAL_ENDPOINT}"
    )


# ---------------------------------------------------------------------------
# Screen capture + OCR
# ---------------------------------------------------------------------------

@mcp_app.tool()
def get_monitor_summary() -> str:
    """List the user's connected displays with resolution, scaling, and which is active.

    Returns one entry per monitor with index, resolution, scale factor, and a flag
    marking the monitor that currently contains the cursor.

    USE WHEN: about to call get_screenshot and need to know which monitor index to
    target, or when debugging multi-monitor setups.
    NOT FOR: capturing pixels — this returns metadata only.
    ALTERNATIVES: get_screenshot(monitor_index=...) to actually capture.

    BEHAVIOR: pure read; no side effects. Result reflects monitor state at call
    time and may change if the user plugs/unplugs displays.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_screenshot(monitor_index: int | None = None) -> str:
    """Capture a screenshot of the active monitor (or a specified monitor) at native resolution.

    Returns a base64-encoded PNG plus capture metadata (timestamp, monitor index,
    resolution).

    USE WHEN: you need pixel-level visual context (UI debugging, screenshot of a
    diagram, evidence of on-screen state).
    NOT FOR: text extraction — use get_screen_text, which is ~5x cheaper in tokens
    and runs OCR locally before returning.
    ALTERNATIVES: get_screen_text (OCR only), get_recent (rolling buffer of past
    captures), get_context_at (point-in-time recall).

    BEHAVIOR: synchronous capture; takes 50-200 ms. Image is also written to the
    rolling buffer (visible via get_recent). No auth or rate limits — local only.

    PARAMETERS:
      monitor_index: 0-based monitor index from get_monitor_summary. Omit (or pass
        None) to capture the monitor that currently contains the cursor.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_screen_text() -> str:
    """Run OCR over the current screen and return extracted text only.

    Returns extracted text grouped by detected region (window/panel) with
    approximate bounding boxes.

    USE WHEN: you need the textual content visible on screen (code, terminal,
    chat, docs) and visual layout doesn't matter.
    NOT FOR: visual content (diagrams, photos) — use get_screenshot.
    ALTERNATIVES: get_screenshot (raw pixels), search_history (OCR over past
    captures).

    BEHAVIOR: captures + OCR in one call; takes 200-800 ms depending on screen
    size. Cheaper in tokens than get_screenshot (~200-700 vs ~1200). Result is
    also indexed into the OCR history (visible via search_history).
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_recent(n: int = 10) -> str:
    """Return the most recent N screenshots from the rolling capture buffer.

    Returns a list of capture entries (timestamp, monitor index, thumbnail
    reference, OCR snippet) ordered newest-first.

    USE WHEN: you need to see what the user was looking at over the last few
    minutes/hours without triggering a fresh capture.
    NOT FOR: live state — use get_screenshot for "right now."
    ALTERNATIVES: get_context_at (specific point in time), search_history (query
    by OCR text).

    BEHAVIOR: pure read from the local buffer. Buffer size and retention are
    governed by daemon config; defaults to ~last 24h. No side effects.

    PARAMETERS:
      n: how many recent captures to return. Range 1-100. Default 10.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_buffer_status() -> str:
    """Report the rolling capture buffer's size, age range, retention policy, and disk usage.

    Returns counts (entries, hours of coverage), bytes on disk, and the configured
    retention window.

    USE WHEN: troubleshooting why search_history returns no results, or before
    requesting historical context that may have aged out.
    NOT FOR: contents of the buffer — use get_recent or search_history for that.

    BEHAVIOR: pure read of buffer metadata. No side effects.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_context_at(minutes_ago: int) -> str:
    """Return screen + activity context from a specific point in the recent past.

    Returns the captured screen state, OCR text, active window, and clipboard
    snapshot from the buffer entry closest to the requested timestamp.

    USE WHEN: the user references something from earlier ("the error I saw 10
    minutes ago", "what was on screen when I started this session") and you need
    to recall that exact state.
    NOT FOR: live state (use get_screenshot) or text-search across history (use
    search_history).
    ALTERNATIVES: get_recent for a chronological list.

    BEHAVIOR: pure read. Returns the closest buffer entry within +/- 30 seconds
    of the requested point; raises if no entry exists.

    PARAMETERS:
      minutes_ago: how many minutes back to look. Range 0-1440 (24h). Required.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def search_history(query: str, limit: int = 20) -> str:
    """Full-text search the OCR history of past screen captures.

    Returns matching entries with timestamp, snippet of matched text, and capture
    reference, ordered by relevance.

    USE WHEN: the user asks "when did I see X" / "find that error message" /
    "show me where I was working on Y."
    NOT FOR: vector similarity (use memory_semantic_search), live screen
    (get_screen_text), or non-text content (get_recent).

    BEHAVIOR: pure read; sub-100 ms for typical buffers. Search is case-insensitive
    and runs against OCR text only — visual elements without text won't match.

    PARAMETERS:
      query: substring or simple SQLite FTS expression. Required, non-empty.
      limit: max results. Range 1-100. Default 20.
    """
    return _LOCAL_ONLY_MSG


# ---------------------------------------------------------------------------
# Activity + project context
# ---------------------------------------------------------------------------

@mcp_app.tool()
def get_activity_summary(hours: int = 1) -> str:
    """Summarize the user's activity (apps, focus time, OCR keyword hits) over the last N hours.

    Returns a structured summary: per-app time-on-task, top OCR keywords, focus
    sessions detected, and idle gaps.

    USE WHEN: the user asks "what have I been doing" / "where did the day go" /
    "summarize my last hour."
    NOT FOR: per-event detail (use get_recent_touch_events) or app-only breakdown
    (use get_app_usage).

    BEHAVIOR: aggregates from buffer + activity log. No side effects. Result
    accuracy depends on buffer coverage; check get_buffer_status if results look
    sparse.

    PARAMETERS:
      hours: lookback window. Range 1-24. Default 1.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_app_usage(hours: int = 1) -> str:
    """Return per-application time-on-task over the last N hours.

    Returns a list of (app_name, foreground_seconds, focus_sessions) entries,
    sorted by foreground time descending.

    USE WHEN: you want a clean app-level breakdown without OCR/keyword data.
    NOT FOR: full activity summary including content — use get_activity_summary.

    BEHAVIOR: pure read from foreground-window log. No side effects. Granularity
    is per-second.

    PARAMETERS:
      hours: lookback window. Range 1-24. Default 1.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_session_summary() -> str:
    """Summarize the current ContextPulse session (since daemon start) — apps, captures, voice.

    Returns aggregate counts (screenshots taken, voice segments transcribed,
    clipboard events, keystrokes) plus the session start timestamp.

    USE WHEN: at the end of a work session and you want a single-call rollup
    without specifying a window.
    NOT FOR: arbitrary windows — use get_activity_summary(hours=N).

    BEHAVIOR: pure read. Session boundary is set by the most recent daemon
    start; restarting ContextPulse resets the counter.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def find_related_context(query: str, limit: int = 10) -> str:
    """Find screen, voice, and clipboard entries semantically related to a query string.

    Returns a unified list of related entries across all three sources, ranked
    by similarity, with source-type tags.

    USE WHEN: open-ended recall ("anything I worked on related to taxes",
    "everything mentioning the new client") that spans multiple data types.
    NOT FOR: single-source search — prefer search_history, search_voice, or
    search_clipboard if you know the type.

    BEHAVIOR: vector search over indexed embeddings. Sub-second for typical
    buffers. Returns no results if embeddings haven't been built yet.

    PARAMETERS:
      query: free-text query. Required, non-empty.
      limit: max results across all sources. Range 1-50. Default 10.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def describe_workspace() -> str:
    """Describe the current desktop workspace state: active app, open windows, monitors.

    Returns a structured snapshot (active app, foreground window title, list of
    visible windows by monitor, time-of-day signal).

    USE WHEN: you need a quick "where am I in the OS right now" without pulling
    pixels or OCR.
    NOT FOR: visual content (use get_screenshot) or text content (use
    get_screen_text).

    BEHAVIOR: pure read. Snapshot is captured at call time; very low cost.
    """
    return _LOCAL_ONLY_MSG


# ---------------------------------------------------------------------------
# Project detection + journal routing
# ---------------------------------------------------------------------------

@mcp_app.tool()
def identify_project(text: str) -> str:
    """Score a text snippet against the indexed project corpus and return the best-match project.

    Returns the top project ID, a confidence score (0.0-1.0), and the next two
    runners-up.

    USE WHEN: the user pastes a question or note and you need to route it to the
    right project's context before answering.
    NOT FOR: detecting the user's CURRENT project — use get_active_project, which
    factors in CWD and window title.

    BEHAVIOR: pure read; runs TF-IDF + project-keyword scoring. No side effects.

    PARAMETERS:
      text: snippet to classify. Required, non-empty. Longer text scores more
        reliably; aim for 50+ characters.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_active_project() -> str:
    """Detect the user's current project from working directory, active window, and recent activity.

    Returns project ID, detection method (cwd / window-title / activity-blend),
    and confidence.

    USE WHEN: starting a session and you need to load the right project's
    context, or when the user says "where am I" / "what am I working on."
    NOT FOR: classifying arbitrary text — use identify_project.

    BEHAVIOR: pure read; combines multiple signals. Returns "unknown" if nothing
    matches above the confidence floor.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def list_projects() -> str:
    """List every project indexed by ContextPulse with name, slug, and a one-line overview.

    Returns one entry per project: id, display name, root path, brief summary,
    last-touched timestamp.

    USE WHEN: showing the user a project picker, or before calling
    get_project_context for a specific project.
    NOT FOR: full content — use get_project_context for that.

    BEHAVIOR: pure read of the project registry. No side effects.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_project_context(project_id: str) -> str:
    """Return the full PROJECT_CONTEXT.md for a specific project.

    Returns the markdown content as a string, including front-matter if present.

    USE WHEN: you need the canonical project description, decisions, and
    architecture before answering a project-specific question.
    NOT FOR: live activity — use get_activity_summary for that.

    BEHAVIOR: pure read. Returns empty string if the project has no
    PROJECT_CONTEXT.md.

    PARAMETERS:
      project_id: project slug as returned by list_projects or get_active_project.
        Required.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def route_to_journal(text: str, project_id: str | None = None) -> str:
    """Route a piece of text to the correct project's journal database.

    Returns the project the entry was routed to, the journal entry ID, and
    storage location.

    USE WHEN: the user wants to log something and you want it filed under the
    right project automatically.
    NOT FOR: identifying the project without writing — use identify_project.
    ALTERNATIVES: passing project_id explicitly to skip auto-detection.

    BEHAVIOR: SIDE EFFECT — writes a row to the per-project journal SQLite
    database. Idempotent only if you pass the same content + project pair.
    Auto-detects the project via identify_project + get_active_project blend
    when project_id is omitted.

    PARAMETERS:
      text: journal entry content. Required, non-empty.
      project_id: explicit project slug to route to. Omit to auto-detect.
    """
    return _LOCAL_ONLY_MSG


# ---------------------------------------------------------------------------
# Voice + dictation
# ---------------------------------------------------------------------------

@mcp_app.tool()
def get_recent_voice(n: int = 10) -> str:
    """Return the most recent N voice transcription segments captured by ContextPulse.

    Returns segments with timestamp, transcript text, confidence score, and
    duration.

    USE WHEN: the user references something they just dictated ("what did I just
    say", "use my last voice note").
    NOT FOR: text search over older voice — use search_voice.
    ALTERNATIVES: search_voice (text query), find_related_context (cross-source).

    BEHAVIOR: pure read from the voice transcript log. No side effects.

    PARAMETERS:
      n: how many segments to return. Range 1-100. Default 10.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def search_voice(query: str, limit: int = 20) -> str:
    """Full-text search over the user's voice transcription history.

    Returns matching segments ranked by relevance with timestamp, transcript
    snippet, and full-segment reference.

    USE WHEN: the user references something they said ("when did I mention X",
    "find the part where I talked about Y").
    NOT FOR: live transcription — ContextPulse transcribes asynchronously; very
    recent audio may not be indexed yet.

    BEHAVIOR: pure read. Substring + FTS search. Sub-second for typical buffers.

    PARAMETERS:
      query: substring or FTS expression. Required, non-empty.
      limit: max results. Range 1-100. Default 20.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_voice_stats(hours: int = 24) -> str:
    """Return dictation quality stats (WPM, confidence average, correction rate) over a window.

    Returns aggregate metrics: words-per-minute, average Whisper confidence,
    correction rate (per get_correction_history), session count.

    USE WHEN: the user asks "how is my dictation going" or you're analyzing
    voice quality trends.
    NOT FOR: per-segment data — use get_recent_voice or search_voice.

    BEHAVIOR: pure read. Returns zero-valued metrics if no voice activity in
    the window.

    PARAMETERS:
      hours: lookback window. Range 1-720 (30d). Default 24.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_correction_history(limit: int = 20) -> str:
    """Return recent voice transcription corrections detected by the user or auto-detected.

    Returns correction events: original transcript, corrected text, confidence
    delta, timestamp, and whether the correction was manual or auto-suggested.

    USE WHEN: training the vocabulary, analyzing systemic Whisper errors, or
    debugging why a specific term keeps mis-transcribing.
    NOT FOR: vocabulary management — use add_to_vocabulary / remove_from_vocabulary.

    BEHAVIOR: pure read. No side effects.

    PARAMETERS:
      limit: max results, ordered newest-first. Range 1-100. Default 20.
    """
    return _LOCAL_ONLY_MSG


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

@mcp_app.tool()
def add_to_vocabulary(term: str, pronunciation: str | None = None) -> str:
    """Add a custom term to ContextPulse's voice vocabulary so Whisper recognizes it correctly.

    Returns the stored term, normalized form, and any pronunciation hint
    accepted.

    USE WHEN: a proper noun or technical term keeps mis-transcribing and you
    want to teach the recognizer.
    NOT FOR: bulk vocabulary loads — use the local CLI for that.
    ALTERNATIVES: remove_from_vocabulary to undo, get_vocabulary to inspect.

    BEHAVIOR: SIDE EFFECT — writes to the vocabulary database. Persists across
    daemon restarts. Idempotent for identical (term, pronunciation) pairs;
    second add updates the entry rather than duplicating.

    PARAMETERS:
      term: the spelling you want Whisper to produce. Required, non-empty.
      pronunciation: optional phonetic hint (CMU dict format or plain English
        approximation). Omit to let ContextPulse infer.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_vocabulary() -> str:
    """List every term in the user's voice vocabulary with pronunciations and add-date.

    Returns one entry per term: spelling, pronunciation hint, source (manual /
    auto-learned), date added.

    USE WHEN: auditing what's been taught, or before adding a term to check for
    duplicates.
    NOT FOR: searching transcript history — use search_voice.

    BEHAVIOR: pure read of the vocabulary database. No side effects.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def remove_from_vocabulary(term: str) -> str:
    """Remove a term from the voice vocabulary.

    Returns confirmation with the removed term and whether the term existed.

    USE WHEN: a vocabulary entry is causing wrong corrections or is no longer
    relevant.
    NOT FOR: temporarily disabling — there is no soft-disable; this is a
    permanent delete.

    BEHAVIOR: SIDE EFFECT — DESTRUCTIVE. Removes the term from the vocabulary
    database; not recoverable except by re-adding. Idempotent (no-op if term
    doesn't exist).

    PARAMETERS:
      term: exact spelling as stored. Required. Case-sensitive — confirm via
        get_vocabulary first.
    """
    return _LOCAL_ONLY_MSG


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

@mcp_app.tool()
def get_clipboard_history(n: int = 10) -> str:
    """Return the most recent N clipboard entries captured by ContextPulse.

    Returns entries with timestamp, content (or content-type if non-text), and
    source application.

    USE WHEN: the user references "what I just copied" or wants to recall
    something they copied earlier in the session.
    NOT FOR: text search — use search_clipboard.

    BEHAVIOR: pure read. Sensitive content (passwords from password managers,
    OAuth tokens detected by pattern) is auto-excluded from the buffer; those
    will not appear here.

    PARAMETERS:
      n: how many entries to return, newest-first. Range 1-100. Default 10.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def search_clipboard(query: str, limit: int = 20) -> str:
    """Full-text search over the clipboard history.

    Returns matching entries with timestamp, snippet, and source application.

    USE WHEN: the user asks "find that thing I copied about X" / "did I copy
    the bug ID."
    NOT FOR: non-text clipboard content — only text entries are indexed.

    BEHAVIOR: pure read. Sensitive entries are excluded from the index (see
    get_clipboard_history).

    PARAMETERS:
      query: substring or FTS expression. Required, non-empty.
      limit: max results. Range 1-100. Default 20.
    """
    return _LOCAL_ONLY_MSG


# ---------------------------------------------------------------------------
# Touch (keyboard + mouse) events
# ---------------------------------------------------------------------------

@mcp_app.tool()
def get_recent_touch_events(n: int = 50) -> str:
    """Return the most recent N keyboard and mouse activity events.

    Returns events with timestamp, type (key/click/scroll), aggregate counts
    (not individual keystrokes — content is not logged), and active app.

    USE WHEN: analyzing input patterns, idle detection, or activity timing.
    NOT FOR: keylogging — actual keystroke contents are NEVER stored, only
    aggregate event metadata.

    BEHAVIOR: pure read. Privacy guarantee: no key contents, no clipboard
    targets, no scroll positions inside sensitive apps.

    PARAMETERS:
      n: number of events. Range 1-500. Default 50.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def get_touch_stats(hours: int = 1) -> str:
    """Return aggregate keyboard and mouse activity stats over the last N hours.

    Returns counts: keystrokes (count only — no contents), mouse clicks,
    scrolls, idle gaps, active-typing minutes.

    USE WHEN: producing activity reports, idle detection, or fatigue tracking.

    BEHAVIOR: pure read. Privacy guarantee as in get_recent_touch_events.

    PARAMETERS:
      hours: lookback window. Range 1-24. Default 1.
    """
    return _LOCAL_ONLY_MSG


# ---------------------------------------------------------------------------
# Memory database (key/value + search)
# ---------------------------------------------------------------------------

@mcp_app.tool()
def memory_store(key: str, value: str, tag: str | None = None) -> str:
    """Persist a string value under a key in ContextPulse's local memory database.

    Returns confirmation with the stored key and an indication of whether the
    write was new or an overwrite.

    USE WHEN: you need a value to survive across sessions and be recallable by
    exact key (preferences, agent memos, named facts).
    NOT FOR: fuzzy retrieval (use memory_search) or semantic similarity (use
    memory_semantic_search). For ephemeral session state, hold it in agent
    context — don't bloat persistent memory.
    ALTERNATIVES: route_to_journal (timestamped narrative log) is better for
    journaling.

    BEHAVIOR: SIDE EFFECT — overwrites any existing value at the same key
    (last-write-wins). Persists to the local SQLite memory database; survives
    daemon restart. Idempotent for identical (key, value, tag) tuples. No auth
    or rate limits — local only.

    PARAMETERS:
      key: stable identifier. ASCII recommended. Max 256 chars. Required.
      value: string to persist. Max ~1 MB. Required.
      tag: optional grouping label, queryable via memory_list(tag=...).
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def memory_recall(key: str) -> str:
    """Recall the value stored at an exact key from the local memory database.

    Returns the stored value as a string, or a "not found" indicator if the key
    doesn't exist.

    USE WHEN: you stored something via memory_store and need to retrieve it by
    its exact key.
    NOT FOR: fuzzy or substring lookup — use memory_search. For semantic
    similarity, use memory_semantic_search.

    BEHAVIOR: pure read. Sub-millisecond. Does NOT update any access timestamp
    — repeated recall is invisible.

    PARAMETERS:
      key: exact key as passed to memory_store. Case-sensitive. Required.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def memory_search(query: str, limit: int = 20) -> str:
    """Full-text substring search over stored memory values.

    Returns matching (key, value, tag, score) entries ranked by relevance.

    USE WHEN: you don't remember the exact key but know a substring of the
    value or its tag.
    NOT FOR: exact-key lookup (use memory_recall) or true semantic similarity
    (use memory_semantic_search).

    BEHAVIOR: pure read. SQLite FTS over the memory table. Sub-100 ms for
    typical sizes.

    PARAMETERS:
      query: substring or FTS expression. Required, non-empty.
      limit: max results. Range 1-100. Default 20.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def memory_semantic_search(query: str, limit: int = 10) -> str:
    """Vector-similarity search over stored memory entries using local embeddings.

    Returns ranked matches with similarity scores (0.0-1.0) and the stored
    key/value/tag.

    USE WHEN: you want conceptually-similar memories, not just substring
    matches ("anything about retirement planning" returns memories tagged
    "401k", "pension", "ira").
    NOT FOR: exact-key retrieval (use memory_recall) or substring lookup (use
    memory_search). Slower and more compute-intensive than memory_search.

    BEHAVIOR: pure read. Uses local sentence-transformers embeddings; first
    call after daemon start may take 1-2 s for model load. Returns no results
    if the embedding index hasn't been built — see memory_stats for build
    state.

    PARAMETERS:
      query: free-text query. Required, non-empty.
      limit: max results. Range 1-50. Default 10.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def memory_list(tag: str | None = None) -> str:
    """List stored memory keys, optionally filtered by tag.

    Returns (key, tag, byte_size, last_updated) entries sorted by last_updated
    descending.

    USE WHEN: auditing what's stored, or before bulk-deleting by tag.
    NOT FOR: retrieving values — use memory_recall (exact) or memory_search
    (fuzzy). This returns metadata only.

    BEHAVIOR: pure read. Sub-millisecond.

    PARAMETERS:
      tag: filter to entries with this exact tag. Omit to list all entries.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def memory_forget(key: str) -> str:
    """Delete a memory entry by exact key.

    Returns confirmation with the deleted key and whether it existed before
    deletion.

    USE WHEN: a stored value is wrong or no longer needed.
    NOT FOR: clearing all memory — there is no clear-all tool by design; loop
    over memory_list and call memory_forget for each key.

    BEHAVIOR: SIDE EFFECT — DESTRUCTIVE. Removes the row from the memory
    database; not recoverable except by re-storing. Idempotent (no-op if key
    doesn't exist).

    PARAMETERS:
      key: exact key as passed to memory_store. Case-sensitive. Required.
    """
    return _LOCAL_ONLY_MSG


@mcp_app.tool()
def memory_stats() -> str:
    """Return statistics about the local memory database (entry count, size, tags, embedding state).

    Returns counts (rows, distinct tags, total bytes), embedding index status
    (built / building / stale), and last-write timestamp.

    USE WHEN: troubleshooting why memory_semantic_search returns no results, or
    sizing the user's local data footprint.

    BEHAVIOR: pure read of metadata. No side effects.
    """
    return _LOCAL_ONLY_MSG


if __name__ == "__main__":
    mcp_app.run(transport="stdio")
