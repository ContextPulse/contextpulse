# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Context vocabulary builder — extracts proper nouns from PROJECT_CONTEXT.md files.

Scans the projects directory for CamelCase project names and domain-specific
terms that Whisper commonly splits into separate words. Generates a vocabulary
file that the Voice module hot-reloads alongside user and learned vocabularies.

Priority: user vocabulary > learned vocabulary > context vocabulary.
"""

import json
import logging
import re
from pathlib import Path

from contextpulse_voice.config import CONTEXT_VOCAB_FILE, VOICE_DATA_DIR

logger = logging.getLogger(__name__)

# Common English words/phrases that should NOT be replaced even if they
# match a CamelCase split. E.g., "island model" is valid English.
_COMMON_PHRASES: set[str] = {
    "personal finance", "stock market", "tax prep", "death planning",
    "island model", "screen context",
}

# Minimum key length in characters to avoid overly aggressive matching.
_MIN_KEY_LENGTH = 6


def _split_camel_to_phrase(name: str) -> str | None:
    """Split CamelCase into a lowercased space-separated phrase.

    Returns None if the result is a single word (no split happened)
    or the key is too short.

    Examples:
        "ContextPulse" → "context pulse"
        "TaskRunner" → "task runner"
        "WeatherApp" → "weather app"
        "AWS" → None (single word)
    """
    # Insert spaces before uppercase letters following lowercase
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    # Also split between sequential uppercase and lowercase: "WeatherApp" → "Dryer Vent Co"
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    parts = spaced.split()
    if len(parts) < 2:
        return None
    phrase = " ".join(parts).lower()
    if len(phrase) < _MIN_KEY_LENGTH:
        return None
    return phrase


def _extract_names_from_context(text: str) -> list[str]:
    """Extract product names and proper nouns from PROJECT_CONTEXT.md content.

    Looks for CamelCase words and quoted product names in the overview section.
    """
    names: list[str] = []
    # Find CamelCase words (2+ parts, 4+ chars each part)
    for match in re.finditer(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", text):
        word = match.group(1)
        if len(word) >= 6:
            names.append(word)
    # Find quoted product names: "ProductName" or 'ProductName'
    for match in re.finditer(r'["\']([A-Z][a-zA-Z]{4,})["\']', text):
        names.append(match.group(1))
    return list(set(names))


def _scan_skills_directory(skills_dir: Path) -> dict[str, str]:
    """Extract domain-specific terms from agent skill files.

    Skills contain technical terminology (kubectl, PostgreSQL, Sharpe ratio)
    and product names that Whisper commonly mangles. Scanning skill content
    gives Voice vocabulary awareness of the user's technical domain.
    """
    vocab: dict[str, str] = {}
    if not skills_dir.is_dir():
        return vocab

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            content = skill_file.read_text(encoding="utf-8")
            # Only scan first 1500 chars (frontmatter + intro)
            content = content[:1500]
            for name in _extract_names_from_context(content):
                phrase = _split_camel_to_phrase(name)
                if phrase and phrase not in _COMMON_PHRASES:
                    if phrase not in vocab:
                        vocab[phrase] = name
        except OSError:
            pass

    if vocab:
        logger.info("Extracted %d terms from skills at %s", len(vocab), skills_dir)
    return vocab


def build_context_vocabulary(
    projects_root: Path | None = None,
    skills_dirs: list[Path] | None = None,
) -> dict[str, str]:
    """Scan project directories and skills to build context-aware vocabulary.

    Args:
        projects_root: Root directory containing project folders.
                      Defaults to ~/Projects.
        skills_dirs: List of skill directories to scan.
                    Defaults to ~/.claude/skills/ and ~/.gemini/skills/.

    Returns:
        Dictionary of whisper-mishearing → correct-spelling entries.
    """
    if projects_root is None:
        projects_root = Path.home() / "Projects"

    if skills_dirs is None:
        skills_dirs = [
            Path.home() / ".claude" / "skills",
            Path.home() / ".gemini" / "skills",
        ]

    vocab: dict[str, str] = {}

    # Scan project directories
    if projects_root.is_dir():
        for child in sorted(projects_root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue

            # Generate entries from directory name (CamelCase → space-separated)
            phrase = _split_camel_to_phrase(child.name)
            if phrase and phrase not in _COMMON_PHRASES:
                vocab[phrase] = child.name

            # Scan PROJECT_CONTEXT.md for additional proper nouns
            ctx_file = child / "PROJECT_CONTEXT.md"
            if ctx_file.exists():
                try:
                    content = ctx_file.read_text(encoding="utf-8")
                    # Only scan first ~2000 chars (overview section)
                    content = content[:2000]
                    for name in _extract_names_from_context(content):
                        name_phrase = _split_camel_to_phrase(name)
                        if name_phrase and name_phrase not in _COMMON_PHRASES:
                            if name_phrase not in vocab:
                                vocab[name_phrase] = name
                except OSError:
                    pass
    else:
        logger.warning("Projects root not found: %s", projects_root)

    # Scan skills directories for domain-specific terms
    for skills_dir in skills_dirs:
        skill_vocab = _scan_skills_directory(skills_dir)
        for key, val in skill_vocab.items():
            if key not in vocab:
                vocab[key] = val

    logger.info(
        "Built context vocabulary: %d entries (projects=%s, skills=%s)",
        len(vocab), projects_root, [str(d) for d in skills_dirs],
    )
    return vocab


def rebuild_context_vocabulary(projects_root: Path | None = None) -> int:
    """Rebuild and write the context vocabulary file.

    Returns the number of entries written.
    """
    vocab = build_context_vocabulary(projects_root)
    VOICE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONTEXT_VOCAB_FILE.write_text(
        json.dumps(vocab, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Wrote %d context vocabulary entries to %s", len(vocab), CONTEXT_VOCAB_FILE)
    return len(vocab)


def get_context_entries() -> dict[str, str]:
    """Return the current context vocabulary entries."""
    if not CONTEXT_VOCAB_FILE.exists():
        return {}
    try:
        data = json.loads(CONTEXT_VOCAB_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_known_proper_nouns() -> list[str]:
    """Return all known proper noun replacements from context vocab.

    Used by the LLM cleanup to provide context hints.
    """
    entries = get_context_entries()
    return sorted(set(entries.values()))
