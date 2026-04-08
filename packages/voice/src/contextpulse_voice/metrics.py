# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Voice accuracy metrics — tracks correction rates and vocabulary growth.

Computes daily accuracy scorecards and appends to a JSONL history file
for trend analysis. Used by both nightly and weekly learning scripts.
"""

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from contextpulse_voice.config import (
    CONTEXT_VOCAB_FILE,
    LEARNED_VOCAB_FILE,
    VOCAB_FILE,
    VOICE_DATA_DIR,
)

logger = logging.getLogger(__name__)

METRICS_FILE = VOICE_DATA_DIR / "metrics_history.jsonl"


def compute_accuracy_scorecard(
    db_path: Path | None = None,
    hours: int = 24,
) -> dict:
    """Compute voice accuracy metrics for the given time window.

    Args:
        db_path: Path to activity.db. Defaults to standard location.
        hours: Look-back window in hours.

    Returns:
        Scorecard dict with counts, rates, and vocabulary sizes.
    """
    if db_path is None:
        from contextpulse_core.config import ACTIVITY_DB_PATH
        db_path = ACTIVITY_DB_PATH

    scorecard = {
        "date": datetime.now().isoformat(),
        "hours": hours,
        "total_transcriptions": 0,
        "corrections_detected": 0,
        "correction_rate": 0.0,
        "vocab_user_size": 0,
        "vocab_learned_size": 0,
        "vocab_context_size": 0,
        "top_corrected_words": [],
    }

    # Count transcriptions and corrections
    if db_path and db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path), timeout=2)
            conn.row_factory = sqlite3.Row
            cutoff = time.time() - (hours * 3600)

            trans_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM events "
                "WHERE modality = 'voice' AND event_type = 'transcription' "
                "AND timestamp > ?",
                (cutoff,),
            ).fetchone()["cnt"]
            scorecard["total_transcriptions"] = trans_count

            corr_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM events "
                "WHERE modality = 'keys' AND event_type = 'correction_detected' "
                "AND timestamp > ?",
                (cutoff,),
            ).fetchone()["cnt"]
            scorecard["corrections_detected"] = corr_count

            if trans_count > 0:
                scorecard["correction_rate"] = round(corr_count / trans_count, 4)

            # Top corrected words
            top_rows = conn.execute(
                "SELECT payload FROM events "
                "WHERE modality = 'keys' AND event_type = 'correction_detected' "
                "AND timestamp > ? ORDER BY timestamp DESC LIMIT 100",
                (cutoff,),
            ).fetchall()
            conn.close()

            word_freq: dict[str, int] = {}
            for row in top_rows:
                try:
                    p = json.loads(row["payload"])
                    word = p.get("original_word", "").strip().lower()
                    if word:
                        word_freq[word] = word_freq.get(word, 0) + 1
                except (json.JSONDecodeError, TypeError):
                    continue

            scorecard["top_corrected_words"] = sorted(
                [{"word": w, "count": c} for w, c in word_freq.items()],
                key=lambda x: -x["count"],
            )[:10]
        except (sqlite3.Error, OSError):
            logger.debug("Failed to compute accuracy scorecard", exc_info=True)

    # Vocabulary sizes
    scorecard["vocab_user_size"] = _count_vocab(VOCAB_FILE)
    scorecard["vocab_learned_size"] = _count_vocab(LEARNED_VOCAB_FILE)
    scorecard["vocab_context_size"] = _count_vocab(CONTEXT_VOCAB_FILE)

    return scorecard


def _count_vocab(path: Path) -> int:
    """Count entries in a JSON vocabulary file."""
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return len(data) if isinstance(data, dict) else 0
    except (json.JSONDecodeError, OSError):
        return 0


def record_metrics(scorecard: dict) -> None:
    """Append scorecard to JSONL history file."""
    VOICE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(METRICS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(scorecard, ensure_ascii=False) + "\n")
    logger.info("Recorded metrics to %s", METRICS_FILE)


def generate_weekly_report(weeks: int = 4) -> dict:
    """Generate a weekly trend report from metrics history.

    Args:
        weeks: Number of weeks of history to analyze.

    Returns:
        Report dict with trends and statistics.
    """
    report = {
        "generated": datetime.now().isoformat(),
        "weeks_analyzed": weeks,
        "entries": 0,
        "correction_rate_trend": [],
        "vocab_growth": {},
        "most_corrected_words": [],
        "insufficient_data": False,
    }

    if not METRICS_FILE.exists():
        report["insufficient_data"] = True
        return report

    # Read all entries
    entries = []
    try:
        with open(METRICS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        report["insufficient_data"] = True
        return report

    if len(entries) < 2:
        report["insufficient_data"] = True
        report["entries"] = len(entries)
        return report

    report["entries"] = len(entries)

    # Correction rate trend (last N entries)
    report["correction_rate_trend"] = [
        {"date": e.get("date", ""), "rate": e.get("correction_rate", 0)}
        for e in entries[-weeks * 7:]
    ]

    # Vocab growth: compare first and last entries
    first, last = entries[0], entries[-1]
    report["vocab_growth"] = {
        "user": last.get("vocab_user_size", 0) - first.get("vocab_user_size", 0),
        "learned": last.get("vocab_learned_size", 0) - first.get("vocab_learned_size", 0),
        "context": last.get("vocab_context_size", 0) - first.get("vocab_context_size", 0),
    }

    # Aggregate most-corrected words
    all_words: dict[str, int] = {}
    for entry in entries[-weeks * 7:]:
        for item in entry.get("top_corrected_words", []):
            w = item.get("word", "")
            if w:
                all_words[w] = all_words.get(w, 0) + item.get("count", 0)

    report["most_corrected_words"] = sorted(
        [{"word": w, "count": c} for w, c in all_words.items()],
        key=lambda x: -x["count"],
    )[:10]

    return report
