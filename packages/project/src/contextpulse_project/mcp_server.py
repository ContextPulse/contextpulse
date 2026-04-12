# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""MCP stdio server exposing project-awareness tools to Claude Code.

Tools:
  identify_project    — score text against all projects, return best match
  get_active_project  — detect which project is in focus (CWD/window title)
  list_projects       — return all indexed projects with overviews
  get_project_context — return full PROJECT_CONTEXT.md for a project
  route_to_journal    — auto-route insight to journal (auto-detects project)
"""

import json
import logging

from mcp.server.fastmcp import FastMCP

from contextpulse_project.detector import ActiveProjectDetector
from contextpulse_project.journal_bridge import JournalBridge
from contextpulse_project.registry import ProjectRegistry
from contextpulse_project.router import ProjectRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("contextpulse.project.mcp")

mcp_app = FastMCP("ContextPulse Project")

_registry = ProjectRegistry()
_router = ProjectRouter(_registry)
_detector = ActiveProjectDetector(_registry)
_journal = JournalBridge()


@mcp_app.tool()
def identify_project(text: str) -> str:
    """Score arbitrary text against all projects and return the best match.

    Use this to determine which project a brainstorm insight, email snippet,
    voice note, or any text belongs to. Returns the top 3 matches with scores.
    """
    matches = _router.route(text, top_n=3)
    if not matches:
        return json.dumps({"project": None, "reason": "No matching project found"})

    best = matches[0]
    result = {
        "project": best.project,
        "score": best.score,
        "matched_keywords": best.matched_keywords,
        "reason": best.reason,
        "alternatives": [
            {"project": m.project, "score": m.score}
            for m in matches[1:]
        ],
    }
    return json.dumps(result, indent=2)


@mcp_app.tool()
def get_active_project(cwd: str = "", window_title: str = "") -> str:
    """Detect which project is currently in focus based on CWD and/or window title.

    If both are empty, returns null. Pass the current working directory
    to get instant project detection for any Claude Code session.
    """
    project = _detector.detect(
        cwd=cwd or None,
        window_title=window_title or None,
    )
    if project:
        info = _registry.get(project)
        return json.dumps({
            "project": project,
            "overview": info.overview[:200] if info else "",
            "source": "cwd" if cwd else "window_title",
        })
    return json.dumps({"project": None, "reason": "Could not detect active project"})


@mcp_app.tool()
def list_projects() -> str:
    """Return all indexed projects with their one-line overview and keyword count.

    Useful for understanding the portfolio before routing insights.
    """
    projects = _registry.list_all()
    items = []
    for p in projects:
        first_line = p.overview.split("\n")[0][:120] if p.overview else "(no overview)"
        items.append(f"- **{p.name}** ({len(p.keywords)} keywords): {first_line}")
    return f"## Projects ({len(items)} total)\n\n" + "\n".join(items)


@mcp_app.tool()
def get_project_context(project: str) -> str:
    """Return the full PROJECT_CONTEXT.md for a specific project.

    Case-insensitive lookup. Returns the raw markdown contents.
    """
    info = _registry.get(project)
    if not info:
        return f"Project '{project}' not found. Use list_projects() to see available projects."
    return info.raw_text


@mcp_app.tool()
def route_to_journal(
    text: str,
    project: str = "",
    entry_type: str = "observation",
    session_id: str = "",
) -> str:
    """Route an insight to the journal. Auto-detects project if not specified.

    Entry types: action-discovered, action-completed, observation,
    decision, context-learned, error-encountered
    """
    # Auto-detect project if not provided
    if not project:
        match = _router.best_match(text)
        if match:
            project = match.project
            auto_note = f" (auto-detected, score={match.score})"
        else:
            return json.dumps({
                "success": False,
                "error": "Could not auto-detect project. Please specify the project name.",
            })
    else:
        auto_note = ""
        # Validate project exists
        if not _registry.get(project):
            return json.dumps({
                "success": False,
                "error": f"Project '{project}' not found. Use list_projects() to see options.",
            })

    ok, msg = _journal.log_insight(
        project=project,
        content=text,
        entry_type=entry_type,
        session_id=session_id,
    )

    return json.dumps({
        "success": ok,
        "project": project + auto_note,
        "entry_type": entry_type,
        "message": msg,
    })


def main():
    logger.info("Starting ContextPulse Project MCP server")
    _registry.scan()
    logger.info("Indexed %d projects", len(_registry.list_all()))
    mcp_app.run(transport="stdio")


if __name__ == "__main__":
    main()
