"""ProjectRegistry — loads and indexes PROJECT_CONTEXT.md files."""

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProjectInfo:
    name: str
    path: Path
    overview: str = ""
    goals: list[str] = field(default_factory=list)
    tech_stack: list[str] = field(default_factory=list)
    keywords: set[str] = field(default_factory=set)
    aliases: list[str] = field(default_factory=list)
    raw_text: str = ""


_SKIP_DIRS = {"_PROJECT_TEMPLATE", ".git", "__pycache__", "node_modules", ".venv"}
_ALIAS_PATTERNS = [
    re.compile(r"(?:formerly|previously|renamed from|was)\s+(\w+)", re.IGNORECASE),
    re.compile(r"\((?:was|née|aka)\s+(\w+)\)", re.IGNORECASE),
]
_DOMAIN_PATTERN = re.compile(r"\b[\w-]+\.(?:com|io|dev|ai|app|co)\b")

# Common English words that match too broadly for routing
_STOP_WORDS = frozenset({
    "about", "above", "added", "after", "also", "been", "before", "being",
    "between", "both", "build", "built", "called", "came", "caused", "check",
    "cloud", "config", "could", "current", "data", "does", "each", "even",
    "every", "field", "file", "first", "flow", "follow", "from", "full",
    "getting", "global", "going", "have", "here", "hold", "http", "https",
    "info", "into", "just", "know", "last", "like", "look", "make", "many",
    "model", "more", "most", "much", "needed", "never", "next", "note",
    "only", "open", "other", "over", "path", "runs", "same", "setup",
    "ship", "show", "some", "spec", "still", "such", "take", "than",
    "that", "them", "then", "they", "this", "tool", "type", "under",
    "used", "uses", "using", "vars", "very", "want", "well", "were",
    "what", "when", "where", "which", "will", "with", "work", "works",
    "would", "your", "base", "based", "basic", "browser", "capture",
    "chat", "code", "cost", "email", "local", "platform", "product",
    "release", "request", "rules", "script", "service", "system", "text",
    "user", "admin", "backend",
})


def _split_camel(name: str) -> set[str]:
    parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", name).split()
    result = {name.lower()}
    for p in parts:
        if len(p) > 2:
            result.add(p.lower())
    return result


def _extract_section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _extract_bullets(section: str) -> list[str]:
    return [
        line.lstrip("- *").strip()
        for line in section.splitlines()
        if line.strip().startswith(("-", "*")) and len(line.strip()) > 3
    ]


def _extract_keywords_from_text(text: str) -> set[str]:
    words = set()
    for word in re.findall(r"\b[a-z][a-z0-9_.-]+\b", text.lower()):
        if len(word) > 3 and word not in _STOP_WORDS:
            words.add(word)
    return words


def _parse_context(path: Path) -> ProjectInfo:
    name = path.parent.name
    raw = path.read_text(encoding="utf-8", errors="replace")

    overview_section = _extract_section(raw, "Overview")
    overview = overview_section.split("\n\n")[0] if overview_section else ""

    goals_section = _extract_section(raw, "Goals")
    goals = _extract_bullets(goals_section)

    tech_section = _extract_section(raw, "Tech Stack")
    tech_stack = _extract_bullets(tech_section)

    # Build keyword set
    keywords = _split_camel(name)

    # Tech stack terms
    for item in tech_stack:
        for word in re.findall(r"\b[a-z][a-z0-9_.-]+\b", item.lower()):
            if len(word) > 2 and word not in _STOP_WORDS:
                keywords.add(word)

    # Goal keywords (first sentence of each, words > 3 chars)
    for goal in goals:
        first_sentence = goal.split(".")[0]
        keywords |= _extract_keywords_from_text(first_sentence)

    # Domain names
    for domain in _DOMAIN_PATTERN.findall(raw):
        keywords.add(domain.lower())

    # Aliases
    aliases = []
    for pat in _ALIAS_PATTERNS:
        for m in pat.finditer(raw):
            alias = m.group(1).strip()
            if alias and alias.lower() != name.lower():
                aliases.append(alias)
                keywords |= _split_camel(alias)

    # Overview keywords (lighter weight)
    if overview:
        keywords |= _extract_keywords_from_text(overview)

    return ProjectInfo(
        name=name,
        path=path.parent,
        overview=overview,
        goals=goals,
        tech_stack=tech_stack,
        keywords=keywords,
        aliases=aliases,
        raw_text=raw,
    )


class ProjectRegistry:
    def __init__(self, projects_root: Path | None = None):
        self.projects_root = projects_root or Path.home() / "Projects"
        self._projects: dict[str, ProjectInfo] = {}

    def scan(self) -> None:
        self._projects.clear()
        if not self.projects_root.is_dir():
            return
        for child in sorted(self.projects_root.iterdir()):
            if not child.is_dir() or child.name in _SKIP_DIRS or child.name.startswith("."):
                continue
            ctx_file = child / "PROJECT_CONTEXT.md"
            if ctx_file.is_file():
                self._projects[child.name] = _parse_context(ctx_file)

    def get(self, name: str) -> ProjectInfo | None:
        if not self._projects:
            self.scan()
        # Case-insensitive lookup
        for k, v in self._projects.items():
            if k.lower() == name.lower():
                return v
        return None

    def list_all(self) -> list[ProjectInfo]:
        if not self._projects:
            self.scan()
        return list(self._projects.values())

    def rescan(self) -> None:
        self.scan()
