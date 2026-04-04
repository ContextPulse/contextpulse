# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""ProjectRouter — scores arbitrary text against all projects."""

import re
from dataclasses import dataclass

from contextpulse_project.registry import ProjectRegistry


@dataclass
class RouteMatch:
    project: str
    score: float
    matched_keywords: list[str]
    reason: str


class ProjectRouter:
    def __init__(self, registry: ProjectRegistry):
        self.registry = registry

    def route(self, text: str, top_n: int = 3) -> list[RouteMatch]:
        text_lower = text.lower()
        text_words = set(re.findall(r"\b[a-z][a-z0-9_.-]+\b", text_lower))

        scores: list[RouteMatch] = []

        for project in self.registry.list_all():
            matched = list(text_words & project.keywords)
            bonus = 0

            # Exact project name match
            if project.name.lower() in text_lower:
                matched.append(f"[name:{project.name}]")
                bonus += 5

            # Alias match
            for alias in project.aliases:
                if alias.lower() in text_lower:
                    matched.append(f"[alias:{alias}]")
                    bonus += 4
                    break

            raw_score = len(matched) + bonus
            if raw_score == 0:
                continue

            # Score = raw matches + bonus (primary sort)
            # Tiebreaker: proportion of project keywords matched (rewards specificity)
            keyword_count = max(len(project.keywords), 1)
            proportion = len(matched) / keyword_count  # 0-1, how much of project matched
            combined = raw_score + proportion  # raw count dominates, proportion breaks ties

            scores.append(RouteMatch(
                project=project.name,
                score=combined,
                matched_keywords=sorted(matched),
                reason=f"Matched {len(matched)} keywords",
            ))

        scores.sort(key=lambda m: m.score, reverse=True)

        # Normalize to 0-1 range relative to top score
        if scores and scores[0].score > 0:
            max_score = scores[0].score
            for m in scores:
                m.score = round(m.score / max_score, 3)

        return scores[:top_n]

    def best_match(self, text: str, min_score: float = 0.0) -> RouteMatch | None:
        matches = self.route(text, top_n=1)
        if matches and matches[0].score >= min_score:
            return matches[0]
        return None
