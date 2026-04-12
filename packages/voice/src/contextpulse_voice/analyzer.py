# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Transcript analyzer — learns patterns from dictation history.

Transcription analyzer. Reads from EventBus (events table in activity.db)
instead of history.jsonl. The analyzer can still be run standalone or auto-triggered.
"""

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from contextpulse_voice.config import (
    LEARNED_VOCAB_FILE,
    USER_PROFILE_FILE,
    VOICE_DATA_DIR,
    get_api_key,
)

logger = logging.getLogger(__name__)


def load_entries_from_eventbus(db_path: Path | None = None) -> list[dict]:
    """Load transcription entries from the EventBus events table."""
    import sqlite3

    if db_path is None:
        from contextpulse_core.config import APPDATA_DIR
        db_path = APPDATA_DIR / "activity.db"

    if not db_path.exists():
        return []

    entries = []
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT payload FROM events WHERE modality = 'voice' "
            "AND event_type = 'transcription' ORDER BY timestamp ASC"
        ).fetchall()
        conn.close()

        for row in rows:
            payload = json.loads(row["payload"])
            entries.append({
                "raw": payload.get("raw_transcript", ""),
                "cleaned": payload.get("transcript", ""),
            })
    except Exception:
        logger.exception("Failed to load entries from EventBus")
    return entries


_STYLE_WORDS = {
    "pretty", "cool", "great", "nice", "good", "awesome", "amazing",
    "yeah", "yep", "yup", "yes", "nope", "no", "nah",
    "kinda", "gonna", "wanna", "gotta", "lemme",
    "okay", "ok", "alright", "sure", "right",
    "just", "really", "very", "quite", "totally",
    "raw", "cleaned",
}


def _is_style_change(raw_word: str, cleaned_word: str) -> bool:
    """Detect if a correction is an LLM style rewrite vs a genuine mishearing."""
    r, c = raw_word.lower().strip(".,!?"), cleaned_word.lower().strip(".,!?")
    if r in _STYLE_WORDS or c in _STYLE_WORDS:
        return True
    if r == c:
        return True
    if len(r) <= 1 or len(c) <= 1:
        return True
    if len(r) > 1 and len(c) > 1:
        ratio = len(r) / len(c)
        if ratio > 3.0 or ratio < 0.33:
            return True
    return False


def find_corrections(entries: list[dict]) -> dict[str, dict]:
    """Find words that were consistently changed between raw and cleaned.

    Returns a dict of {raw_word: {replacement, count, confidence}}.
    """
    corrections: dict[str, Counter] = {}

    for entry in entries:
        raw = entry.get("raw", "")
        cleaned = entry.get("cleaned", "")
        if not raw or not cleaned or raw == cleaned:
            continue

        raw_words = raw.lower().split()
        cleaned_words = cleaned.lower().split()

        if len(raw_words) > 3 and len(cleaned_words) > 3:
            ratio = len(cleaned_words) / len(raw_words)
            if ratio > 1.3 or ratio < 0.7:
                continue

        raw_set = Counter(raw_words)
        cleaned_set = Counter(cleaned_words)

        only_raw = []
        only_cleaned = []
        for word in raw_set:
            diff = raw_set[word] - cleaned_set.get(word, 0)
            if diff > 0:
                only_raw.extend([word] * diff)
        for word in cleaned_set:
            diff = cleaned_set[word] - raw_set.get(word, 0)
            if diff > 0:
                only_cleaned.extend([word] * diff)

        if len(only_raw) == 1 and len(only_cleaned) == 1:
            raw_word = only_raw[0]
            cleaned_word = only_cleaned[0]
            if raw_word.strip(".,!?") == cleaned_word.strip(".,!?"):
                continue
            if _is_style_change(raw_word, cleaned_word):
                continue
            if raw_word not in corrections:
                corrections[raw_word] = Counter()
            corrections[raw_word][cleaned_word] += 1

    results = {}
    for raw_word, replacements in corrections.items():
        top_replacement, count = replacements.most_common(1)[0]
        total = sum(replacements.values())
        confidence = count / total
        if count >= 3 and confidence >= 0.7:
            results[raw_word] = {
                "replacement": top_replacement,
                "count": count,
                "confidence": round(confidence, 2),
            }
    return results


def find_frequent_terms(entries: list[dict], top_n: int = 50) -> list[tuple[str, int]]:
    """Find the most frequently used multi-word terms and proper nouns."""
    bigram_counts: Counter = Counter()
    proper_nouns: Counter = Counter()

    for entry in entries:
        text = entry.get("cleaned", "") or entry.get("raw", "")
        words = text.split()

        for i, word in enumerate(words):
            clean_word = word.strip(".,!?;:'\"")
            if (
                i > 0
                and clean_word
                and clean_word[0].isupper()
                and not clean_word.isupper()
                and len(clean_word) > 2
            ):
                proper_nouns[clean_word] += 1

        for i in range(len(words) - 1):
            bigram = f"{words[i].lower().strip('.,!?')} {words[i+1].lower().strip('.,!?')}"
            if len(bigram) > 5:
                bigram_counts[bigram] += 1

    combined: Counter = Counter()
    for term, count in proper_nouns.items():
        if count >= 3:
            combined[term] = count
    for term, count in bigram_counts.most_common(top_n):
        if count >= 5:
            combined[term] = count

    return combined.most_common(top_n)


def build_user_profile(entries: list[dict]) -> dict:
    """Build a user profile from dictation patterns."""
    if not entries:
        return {}

    word_counts = [len(e.get("raw", "").split()) for e in entries if e.get("raw")]
    avg_words = sum(word_counts) / len(word_counts) if word_counts else 0

    frequent = find_frequent_terms(entries, top_n=30)

    return {
        "total_dictations": len(entries),
        "avg_words_per_dictation": round(avg_words, 1),
        "frequent_terms": [
            {"term": t, "count": c}
            for t, c in frequent[:20]
        ],
    }


def analyze_with_llm(entries: list[dict]) -> dict[str, str] | None:
    """Use Claude to analyze transcripts and suggest vocabulary entries."""
    api_key = get_api_key()
    if not api_key:
        logger.info("No API key — skipping LLM analysis")
        return None

    diffs = [
        e for e in entries
        if e.get("raw") and e.get("cleaned") and e.get("raw") != e.get("cleaned")
    ]
    sample = diffs[-100:] if len(diffs) > 100 else diffs

    if len(sample) < 10:
        logger.info("Not enough correction data for LLM analysis (%d entries)", len(sample))
        return None

    examples = "\n".join(
        f"RAW: {e['raw'][:200]}\nCLEANED: {e['cleaned'][:200]}\n"
        for e in sample[-50:]
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": (
                    "Analyze these voice dictation transcripts (RAW = what speech-to-text produced, "
                    "CLEANED = what the user actually meant). Find patterns where the speech-to-text "
                    "engine consistently MISHEARS specific words or phrases.\n\n"
                    "ONLY include genuine mishearings — words that SOUND similar but are spelled wrong. "
                    "Examples: 'jonh' -> 'John', 'system tree' -> 'system tray'.\n\n"
                    "DO NOT include style/tone changes, grammar fixes, filler word removal, "
                    "capitalization-only changes, or sentence restructuring.\n\n"
                    "Return ONLY a JSON object mapping misheard words/phrases to their correct form. "
                    "Only include patterns you see repeated multiple times.\n"
                    '{"misheard phrase": "correct phrase"}\n\n'
                    f"Transcripts:\n{examples}"
                ),
            }],
        )
        text = response.content[0].text.strip()
        match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
        if match:
            suggestions = json.loads(match.group())
            if isinstance(suggestions, dict):
                logger.info("LLM suggested %d vocabulary entries", len(suggestions))
                return suggestions
    except Exception:
        logger.exception("LLM analysis failed")
    return None


def _load_existing_vocab() -> dict[str, str]:
    """Load user vocabulary to avoid suggesting duplicates."""
    from contextpulse_voice.config import VOCAB_FILE
    vocab: dict[str, str] = {}
    for path in [VOCAB_FILE, LEARNED_VOCAB_FILE]:
        if path.exists():
            try:
                vocab.update(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
    return vocab


def run(db_path: Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Run the full analysis pipeline.

    Reads from EventBus events table instead of history.jsonl.
    """
    entries = load_entries_from_eventbus(db_path)
    if not entries:
        logger.info("No history entries to analyze")
        return {"status": "empty"}

    logger.info("Analyzing %d history entries...", len(entries))

    local_corrections = find_corrections(entries)
    logger.info("Found %d local correction patterns", len(local_corrections))

    llm_suggestions = analyze_with_llm(entries)
    profile = build_user_profile(entries)

    existing_vocab = _load_existing_vocab()
    new_entries: dict[str, str] = {}

    for raw_word, info in local_corrections.items():
        replacement = info["replacement"]
        if raw_word not in existing_vocab and replacement != raw_word:
            if len(raw_word.split()) > 3 or len(replacement.split()) > 3:
                continue
            if (len(raw_word.split()) == 1 and len(replacement.split()) == 1
                    and len(raw_word) <= 6 and len(replacement) <= 6):
                overlap = set(raw_word) & set(replacement.lower())
                if len(overlap) < max(len(raw_word), len(replacement)) * 0.4:
                    continue
            new_entries[raw_word] = replacement

    if llm_suggestions:
        for misheard, correct in llm_suggestions.items():
            key = misheard.lower().strip()
            val = correct.strip()
            if not key or not val or key in existing_vocab:
                continue
            if key == val.lower():
                continue
            if _is_style_change(key, val.lower()):
                continue
            if len(key.split()) > 4 or len(val.split()) > 4:
                continue
            if (len(key.split()) == 1 and len(val.split()) == 1
                    and len(key) <= 6 and len(val) <= 6):
                overlap = set(key) & set(val.lower())
                if len(overlap) < max(len(key), len(val)) * 0.4:
                    continue
            new_entries[key] = val

    summary: dict[str, Any] = {
        "status": "ok",
        "entries_analyzed": len(entries),
        "local_corrections": len(local_corrections),
        "llm_suggestions": len(llm_suggestions) if llm_suggestions else 0,
        "new_vocab_entries": len(new_entries),
        "profile": profile,
    }

    if dry_run:
        summary["would_add"] = new_entries
    else:
        VOICE_DATA_DIR.mkdir(parents=True, exist_ok=True)

        learned: dict[str, str] = {}
        if LEARNED_VOCAB_FILE.exists():
            try:
                learned = json.loads(LEARNED_VOCAB_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        learned.update(new_entries)
        if learned:
            LEARNED_VOCAB_FILE.write_text(
                json.dumps(learned, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info("Saved %d learned vocab entries", len(learned))

        USER_PROFILE_FILE.write_text(
            json.dumps(profile, indent=2), encoding="utf-8"
        )
        summary["added"] = new_entries

    return summary
