# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""Nightly vocabulary consolidation — orchestrates cross-modal learning.

Runs all harvesting modules, performs cross-modal correction mining
(the unique value of having sight+voice+touch in one system), deduplicates
vocabulary layers, and emits audit events.
"""

import json
import logging
import shutil
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


def consolidate_vocabulary(
    db_path: Path | None = None,
    dry_run: bool = True,
) -> dict:
    """Orchestrate the full nightly vocabulary consolidation pipeline.

    Steps:
        1. Backup vocab files
        2. Session learning (transcript patterns)
        3. Cross-modal correction mining (the unique value)
        4. OCR term harvesting
        5. Clipboard term harvesting
        6. Repeated correction escalation
        7. Context vocabulary rebuild
        8. Deduplicate vocab layers
        9. Emit audit event

    Args:
        db_path: Path to activity.db. Defaults to standard location.
        dry_run: If True, analyze only — no vocab file writes.

    Returns:
        Summary dict with counts for each step.
    """
    if db_path is None:
        from contextpulse_core.config import ACTIVITY_DB_PATH
        db_path = ACTIVITY_DB_PATH

    summary = {
        "session_learned": 0,
        "cross_modal": 0,
        "ocr_harvested": 0,
        "clipboard_harvested": 0,
        "escalated": 0,
        "context_rebuilt": 0,
        "deduped": 0,
        "dry_run": dry_run,
        "timestamp": datetime.now().isoformat(),
    }

    # Step 1: Backup
    if not dry_run:
        _backup_vocab_files()

    # Step 2: Session learning (transcript patterns)
    try:
        from contextpulse_voice.session_learner import learn_from_transcription_history
        results = learn_from_transcription_history(
            db_path=db_path, hours=24, dry_run=dry_run,
        )
        summary["session_learned"] = len(results)
    except Exception:
        logger.exception("Session learning failed")

    # Step 3: Cross-modal correction mining
    try:
        cross_modal = _cross_modal_correction_mining(
            db_path=db_path, hours=24, dry_run=dry_run,
        )
        summary["cross_modal"] = len(cross_modal)
    except Exception:
        logger.exception("Cross-modal mining failed")

    # Step 4: OCR harvesting
    try:
        from contextpulse_voice.ocr_harvester import harvest_ocr_terms
        results = harvest_ocr_terms(db_path=db_path, hours=24, dry_run=dry_run)
        summary["ocr_harvested"] = len(results)
    except Exception:
        logger.exception("OCR harvesting failed")

    # Step 5: Clipboard harvesting
    try:
        from contextpulse_voice.clipboard_harvester import harvest_clipboard_terms
        results = harvest_clipboard_terms(db_path=db_path, hours=24, dry_run=dry_run)
        summary["clipboard_harvested"] = len(results)
    except Exception:
        logger.exception("Clipboard harvesting failed")

    # Step 6: Escalation
    try:
        from contextpulse_voice.escalation import check_repeated_corrections
        results = check_repeated_corrections(
            db_path=db_path, hours=72, dry_run=dry_run,
        )
        summary["escalated"] = len(results)
    except Exception:
        logger.exception("Escalation check failed")

    # Step 7: Context vocabulary rebuild
    if not dry_run:
        try:
            from contextpulse_voice.context_vocab import rebuild_context_vocabulary
            count = rebuild_context_vocabulary()
            summary["context_rebuilt"] = count
        except Exception:
            logger.exception("Context vocab rebuild failed")

    # Step 8: Deduplicate
    if not dry_run:
        try:
            summary["deduped"] = _deduplicate_vocab_layers()
        except Exception:
            logger.exception("Vocab deduplication failed")

    # Step 9: Emit audit event
    try:
        _emit_consolidation_event(summary)
    except Exception:
        logger.debug("Failed to emit consolidation event", exc_info=True)

    logger.info("Consolidation complete: %s", summary)
    return summary


def _backup_vocab_files() -> None:
    """Create timestamped backups of all vocabulary files."""
    VOICE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = VOICE_DATA_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)

    for src in (VOCAB_FILE, LEARNED_VOCAB_FILE, CONTEXT_VOCAB_FILE):
        if src.exists():
            dst = backup_dir / f"{src.stem}_{stamp}{src.suffix}"
            shutil.copy2(src, dst)
            logger.debug("Backed up %s → %s", src.name, dst.name)


def _cross_modal_correction_mining(
    db_path: Path,
    hours: int = 24,
    dry_run: bool = True,
) -> list[dict]:
    """Mine cross-modal temporal correlations for high-confidence vocabulary.

    For each TRANSCRIPTION event, look for CORRECTION_DETECTED events within
    30s AND OCR_RESULT events within 5s. If OCR text at dictation time contains
    a CamelCase word matching the correction target, that's a screen-verified
    vocabulary entry (confidence=0.95).

    This is the unique value of having sight+voice+touch in one system.
    """
    import re

    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path), timeout=2)
    conn.row_factory = sqlite3.Row
    cutoff = time.time() - (hours * 3600)

    # Get transcription events
    transcriptions = conn.execute(
        "SELECT timestamp, payload FROM events "
        "WHERE modality = 'voice' AND event_type = 'transcription' "
        "AND timestamp > ? ORDER BY timestamp DESC",
        (cutoff,),
    ).fetchall()

    if not transcriptions:
        conn.close()
        return []

    results = []
    camel_re = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b")

    for trans in transcriptions:
        t = trans["timestamp"]

        # Find corrections within 30s after transcription
        corrections = conn.execute(
            "SELECT payload FROM events "
            "WHERE modality = 'keys' AND event_type = 'correction_detected' "
            "AND timestamp > ? AND timestamp < ?",
            (t, t + 30),
        ).fetchall()

        if not corrections:
            continue

        # Find screen context within 5s of transcription
        screens = conn.execute(
            "SELECT payload FROM events "
            "WHERE modality = 'sight' AND event_type = 'ocr_result' "
            "AND timestamp > ? AND timestamp < ?",
            (t - 5, t + 5),
        ).fetchall()

        # Combine OCR text from nearby screens
        ocr_text = ""
        for s in screens:
            try:
                sp = json.loads(s["payload"])
                if sp.get("ocr_confidence", 0) >= 0.75:
                    ocr_text += " " + sp.get("ocr_text", "")
            except (json.JSONDecodeError, TypeError):
                continue

        if not ocr_text:
            continue

        # Find CamelCase words in OCR text
        ocr_camel = {m.group(1) for m in camel_re.finditer(ocr_text)}

        for corr in corrections:
            try:
                cp = json.loads(corr["payload"])
            except (json.JSONDecodeError, TypeError):
                continue

            corrected = cp.get("corrected_word", "").strip()
            original = cp.get("original_word", "").strip().lower()

            if not corrected or not original:
                continue

            # Check if the corrected word appears in OCR text (screen-verified)
            if corrected in ocr_camel:
                results.append({
                    "original": original,
                    "corrected": corrected,
                    "confidence": 0.95,
                    "source": "cross_modal_screen_verified",
                })
            elif any(corrected.lower() in cw.lower() for cw in ocr_camel):
                # Partial match (app-context-aware)
                results.append({
                    "original": original,
                    "corrected": corrected,
                    "confidence": 0.80,
                    "source": "cross_modal_app_context",
                })

    conn.close()

    if not dry_run and results:
        _write_cross_modal_learned(results)

    logger.info(
        "Cross-modal mining: %d transcriptions, %d corrections found (%s)",
        len(transcriptions), len(results), "dry_run" if dry_run else "applied",
    )
    return results


def _write_cross_modal_learned(corrections: list[dict]) -> None:
    """Write cross-modal corrections to vocabulary_learned.json."""
    VOICE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    existing: dict[str, str] = {}
    if LEARNED_VOCAB_FILE.exists():
        try:
            existing = json.loads(LEARNED_VOCAB_FILE.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (json.JSONDecodeError, OSError):
            existing = {}

    added = 0
    for item in corrections:
        key = item["original"]
        if key not in existing and len(key) >= 6:
            existing[key] = item["corrected"]
            added += 1
            logger.info(
                "Cross-modal learned: %r -> %r (confidence=%.2f, source=%s)",
                key, item["corrected"], item["confidence"], item["source"],
            )

    if added:
        LEARNED_VOCAB_FILE.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _deduplicate_vocab_layers() -> int:
    """Remove context entries that duplicate user or learned entries.

    Priority: user > learned > context. If a key exists in a higher-priority
    layer, remove it from context.

    Returns:
        Number of entries removed from context vocab.
    """
    # Load user vocab
    user: dict[str, str] = {}
    if VOCAB_FILE.exists():
        try:
            user = json.loads(VOCAB_FILE.read_text(encoding="utf-8"))
            if not isinstance(user, dict):
                user = {}
        except (json.JSONDecodeError, OSError):
            user = {}

    # Load learned vocab
    learned: dict[str, str] = {}
    if LEARNED_VOCAB_FILE.exists():
        try:
            learned = json.loads(LEARNED_VOCAB_FILE.read_text(encoding="utf-8"))
            if not isinstance(learned, dict):
                learned = {}
        except (json.JSONDecodeError, OSError):
            learned = {}

    # Load context vocab
    context: dict[str, str] = {}
    if CONTEXT_VOCAB_FILE.exists():
        try:
            context = json.loads(CONTEXT_VOCAB_FILE.read_text(encoding="utf-8"))
            if not isinstance(context, dict):
                context = {}
        except (json.JSONDecodeError, OSError):
            context = {}

    # Remove duplicates
    to_remove = []
    for key in context:
        if key in user or key in learned:
            to_remove.append(key)

    for key in to_remove:
        del context[key]

    if to_remove:
        CONTEXT_VOCAB_FILE.write_text(
            json.dumps(context, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Deduped %d context entries (duplicated in user/learned)", len(to_remove))

    return len(to_remove)


def _emit_consolidation_event(summary: dict) -> None:
    """Emit a LEARNING_CONSOLIDATION event to the EventBus."""
    try:
        from contextpulse_core.spine import ContextEvent, EventType, Modality
        from contextpulse_core.spine.event_bus import EventBus

        event = ContextEvent(
            modality=Modality.SYSTEM,
            event_type=EventType.LEARNING_CONSOLIDATION,
            payload=summary,
        )
        bus = EventBus()
        bus.emit(event)
    except Exception:
        logger.debug("Could not emit consolidation event", exc_info=True)
