# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Background OCR processing thread.

Processes buffer frames through OCR asynchronously and stores results
in the ActivityDB and as .txt sidecars alongside the frame files.

Storage modes (configured via CONTEXTPULSE_STORAGE_MODE):
  smart  — Always store text (searchable index). Delete image only if text-heavy.
  visual — Skip OCR entirely, keep images only.
  both   — Always keep image AND text. Good for debugging/benchmarking.
  text   — Always try text-only; keep image only if OCR fails.

In all modes except "visual", OCR text is always stored when found — it's
tiny (~3-5KB) and makes every frame searchable. The only question is whether
to also keep the image.
"""

import logging
import queue
import threading
from pathlib import Path

from PIL import Image

from contextpulse_sight.activity import ActivityDB
from contextpulse_sight.buffer import RollingBuffer
from contextpulse_sight.classifier import classify_and_extract
from contextpulse_sight.config import ALWAYS_BOTH_APPS, STORAGE_MODE
from contextpulse_sight.redact import redact_sensitive

logger = logging.getLogger("contextpulse.sight.ocr_worker")


class OCRWorker:
    """Background thread that processes frames through OCR and stores results."""

    def __init__(self, activity_db: ActivityDB, buffer: RollingBuffer):
        self._queue: queue.Queue = queue.Queue(maxsize=10)
        self._activity_db = activity_db
        self._buffer = buffer
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._sight_module = None  # optional dual-write to EventBus

    def set_sight_module(self, module) -> None:
        """Attach a SightModule for dual-write EventBus emission."""
        self._sight_module = module

    def enqueue(
        self,
        frame_path: Path,
        row_id: int,
        app_name: str = "",
        window_title: str = "",
        native_img: "Image.Image | None" = None,
    ):
        """Queue a frame for OCR. Non-blocking; drops if queue full.

        Args:
            native_img: Optional native-resolution PIL Image for higher-quality
                OCR. If provided, OCR runs on this instead of the downscaled
                JPEG on disk. The image is NOT saved — only used for OCR.
        """
        if STORAGE_MODE == "visual":
            return  # No OCR needed in visual-only mode
        try:
            self._queue.put_nowait((frame_path, row_id, app_name, window_title, native_img))
        except queue.Full:
            logger.debug("OCR queue full, skipping frame %s", frame_path)

    def start(self):
        """Start the background OCR processing thread."""
        self._thread.start()
        logger.info("OCR worker started (mode=%s, queue=%d)", STORAGE_MODE, self._queue.maxsize)

    def is_alive(self) -> bool:
        """Return True if the OCR processing thread is running."""
        return self._thread.is_alive()

    def stop(self):
        """Stop the OCR processing thread."""
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                frame_path, row_id, app_name, window_title, native_img = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._process(frame_path, row_id, app_name, window_title, native_img)
            except Exception:
                logger.debug("OCR processing failed for %s", frame_path, exc_info=True)

    def _process(
        self,
        frame_path: Path,
        row_id: int,
        app_name: str = "",
        window_title: str = "",
        native_img: "Image.Image | None" = None,
    ):
        """Run OCR on a frame, apply storage mode logic.

        Text is ALWAYS stored when found (DB + .txt sidecar) — it's the
        searchable index. The image is kept or deleted based on storage mode
        and app-level overrides.

        If native_img is provided, OCR runs on the full-resolution image
        for better accuracy (instead of the downscaled JPEG on disk).
        """
        if not frame_path.exists():
            return

        if native_img is not None:
            img = native_img
        else:
            img = Image.open(frame_path)
        result = classify_and_extract(img)
        if native_img is None:
            img.close()  # Release file handle before potential deletion

        has_text = result.get("text") and result.get("chars", 0) > 0
        is_text_heavy = result["type"] == "text" and result["text"]

        # Check if this app always needs both image + text (charts, design tools)
        force_both = app_name.lower() in ALWAYS_BOTH_APPS if app_name else False

        # Redact sensitive patterns (API keys, passwords, etc.) before storage
        if has_text:
            from contextpulse_core.config import get as cfg_get
            if cfg_get("redact_ocr_text", True):
                result["text"] = redact_sensitive(result["text"])

        # Always store whatever text OCR found — it's the searchable metadata
        if has_text:
            self._activity_db.update_ocr(
                row_id, result["text"], result["confidence"]
            )
            self._buffer.add_ocr_text(
                frame_path, result["text"], result["confidence"]
            )
            # Dual-write: emit OCR result to EventBus
            if self._sight_module:
                self._sight_module.emit_ocr(
                    timestamp=frame_path.stat().st_mtime if frame_path.exists() else 0,
                    frame_path=str(frame_path),
                    ocr_text=result["text"],
                    confidence=result["confidence"],
                    app_name=app_name,
                    window_title=window_title,
                )

        # Decide whether to keep the image
        if is_text_heavy and STORAGE_MODE in ("smart", "text") and not force_both:
            # Text-heavy frame: text alone is sufficient, drop the image
            img_kb = 0
            try:
                img_kb = frame_path.stat().st_size // 1024
                frame_path.unlink()
            except (FileNotFoundError, OSError):
                pass
            logger.debug(
                "Text-only: %s (%d chars, %.0f%% conf, saved %dKB)",
                frame_path.name, result["chars"],
                result["confidence"] * 100, img_kb,
            )
        else:
            reason = "app-override" if force_both else result["type"]
            logger.debug(
                "Image+text: %s (reason=%s, %d chars, %.2f conf)",
                frame_path.name, reason,
                result.get("chars", 0), result.get("confidence", 0),
            )
