# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""ContextPulse Project — project-aware routing layer for AI agents."""

__version__ = "0.1.0"

from contextpulse_project.detector import ActiveProjectDetector
from contextpulse_project.journal_bridge import JournalBridge
from contextpulse_project.registry import ProjectInfo, ProjectRegistry
from contextpulse_project.router import ProjectRouter, RouteMatch

__all__ = [
    "ProjectInfo",
    "ProjectRegistry",
    "ProjectRouter",
    "RouteMatch",
    "ActiveProjectDetector",
    "JournalBridge",
]
