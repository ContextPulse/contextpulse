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
    "personal finance",
    "stock market",
    "tax prep",
    "death planning",
    "island model",
    "screen context",
}

# Minimum key length in characters to avoid overly aggressive matching.
_MIN_KEY_LENGTH = 6

# Guards applied only to OCR/clipboard-harvested phrases (see
# is_safe_harvested_phrase below) -- NOT to the project/skill directory scan,
# which only ever reads names David deliberately chose.
_MAX_HARVESTED_WORDS = 2
_MAX_HARVESTED_WORD_LENGTH = 12


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


def is_safe_harvested_phrase(phrase: str) -> bool:
    """Guard for OCR/clipboard-harvested phrases before they enter the vocabulary.

    Harvested phrases come from arbitrary incidental screen/clipboard text, unlike
    the project/skill directory scan in build_context_vocabulary(), which only ever
    reads names David deliberately chose -- so this guard is NOT applied there.

    Rejects:
    - Phrases with more than _MAX_HARVESTED_WORDS words. An ordinary multi-word
      English phrase (e.g. "day trading strategies", from the "DayTradingStrategies"
      CamelCase run the OCR/clipboard regex matched) is far more likely to be an
      incidental sentence fragment than a coined proper noun, and becoming a
      permanent Whisper vocabulary substitution corrupts unrelated dictation
      (David said "regarding day trading" and the paster wrote "regarding
      DayTrading" -- 2026-08-21).
    - Phrases containing a "word" longer than _MAX_HARVESTED_WORD_LENGTH characters.
      This is the signature of OCR whitespace-collapse: when OCR drops the space
      between two capitalized words, the CamelCase regex still matches
      (e.g. "So I have" -> "SoIhave" -> split gives "so" + "ihave", the second
      token abnormally long for a real English word).
    """
    words = phrase.split()
    if not words:
        return False
    if len(words) > _MAX_HARVESTED_WORDS:
        return False
    return all(len(w) <= _MAX_HARVESTED_WORD_LENGTH for w in words)


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
        len(vocab),
        projects_root,
        [str(d) for d in skills_dirs],
    )
    return vocab


def merge_terms_to_context_vocab(terms: list[dict]) -> int:
    """Merge harvested terms into the context vocabulary file (additive only).

    Every incoming phrase is checked by is_safe_harvested_phrase() -- this is the
    single funnel both the OCR and clipboard harvesters write through, so it is
    the authoritative gate regardless of whether a caller's own pre-filtering
    drifts.

    Args:
        terms: Dicts carrying at least ``phrase`` (the key) and ``term``
               (the correct spelling), as produced by the OCR and clipboard
               harvesters.

    Returns:
        Number of entries newly added.
    """
    VOICE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    existing = get_context_entries()

    added = 0
    for item in terms:
        key = item.get("phrase")
        value = item.get("term")
        if not key or not value or key in existing:
            continue
        if not is_safe_harvested_phrase(key):
            logger.info("Rejected unsafe harvested phrase: %r -> %r", key, value)
            continue
        existing[key] = value
        added += 1

    if added:
        CONTEXT_VOCAB_FILE.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return added


def find_stale_harvested_garbage(
    entries: dict[str, str] | None = None,
    projects_root: Path | None = None,
    skills_dirs: list[Path] | None = None,
) -> dict[str, str]:
    """Identify pre-guard entries that is_safe_harvested_phrase() would reject today.

    merge_terms_to_context_vocab() is additive-only (see its own docstring), and
    is_safe_harvested_phrase() was added after OCR/clipboard harvesting had
    already been accumulating entries -- so phrases harvested before the guard
    existed are still sitting in the live file even though the current guard
    would reject them (cp-vocab-context-file-has-existing-garbage).

    An entry is flagged only if BOTH:
      1. it is NOT reproduced by a fresh directory/skills scan. Scan-sourced
         entries never go through is_safe_harvested_phrase() at all -- see that
         function's own docstring -- so a legitimate long or 3+-word project or
         skill name must never be flagged here just because it resembles the
         shape the guard rejects.
      2. is_safe_harvested_phrase(key) is False today.

    This is deliberately conservative and read-only: it never deletes anything
    itself, and it never flags anything a scan can explain. See
    scripts/cleanup_vocab_garbage.py for the reviewed, backed-up, dry-run-first
    cleanup that consumes this.
    """
    if entries is None:
        entries = get_context_entries()
    scan_vocab = build_context_vocabulary(projects_root, skills_dirs=skills_dirs)
    return {
        key: value
        for key, value in entries.items()
        if key not in scan_vocab and not is_safe_harvested_phrase(key)
    }


def rebuild_context_vocabulary(
    projects_root: Path | None = None,
    preserve_existing: bool = True,
) -> int:
    """Rebuild and write the context vocabulary file.

    Args:
        projects_root: Root directory containing project folders.
        preserve_existing: If True (default), entries already in the file that
            the directory scan did not produce are kept. Harvested terms (OCR,
            clipboard) live in this same file, so a non-preserving rebuild
            silently discards every term harvested since the last rebuild.
            Scanned entries win on key collisions, so renames still propagate.

    Returns:
        The number of entries written.
    """
    vocab = build_context_vocabulary(projects_root)

    if preserve_existing:
        merged = get_context_entries()
        kept = len(set(merged) - set(vocab))
        merged.update(vocab)
        vocab = merged
        logger.info("Context vocab rebuild preserved %d non-scanned entries", kept)

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
        data = json.loads(CONTEXT_VOCAB_FILE.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_known_proper_nouns() -> list[str]:
    """Return all known proper noun replacements from context vocab.

    Used by the LLM cleanup to provide context hints.
    """
    entries = get_context_entries()
    return sorted(set(entries.values()))
