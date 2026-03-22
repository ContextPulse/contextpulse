"""SightModule — Adapts the existing Sight capture pipeline to the spine contract.

This module wraps the existing capture, OCR, clipboard, and event detection
components to emit ContextEvent objects through the EventBus. It does NOT
replace the existing code — it wraps it for dual-write during the transition.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from contextpulse_core.spine import (
    ContextEvent,
    EventType,
    Modality,
    ModalityModule,
)

logger = logging.getLogger(__name__)


class SightModule(ModalityModule):
    """Wraps existing Sight capture pipeline to emit ContextEvents.

    Usage:
        module = SightModule()
        module.register(event_bus.emit)
        module.start()

        # In auto-capture loop, after activity_db.record():
        module.emit_capture(ts, app, title, idx, path, diff)

        # In OCR worker, after activity_db.update_ocr():
        module.emit_ocr(ts, path, text, confidence)

        # In clipboard monitor, after activity_db.record_clipboard():
        module.emit_clipboard(ts, text, hash_val)
    """

    def __init__(self) -> None:
        self._callback: Callable[[ContextEvent], None] | None = None
        self._running = False
        self._events_emitted = 0
        self._last_timestamp: float | None = None
        self._error: str | None = None

    def get_modality(self) -> Modality:
        return Modality.SIGHT

    def register(self, event_callback: Callable[[ContextEvent], None]) -> None:
        self._callback = event_callback

    def start(self) -> None:
        self._running = True
        self._error = None
        logger.info("SightModule started")

    def stop(self) -> None:
        self._running = False
        logger.info("SightModule stopped")

    def is_alive(self) -> bool:
        return self._running

    def get_status(self) -> dict[str, Any]:
        return {
            "modality": "sight",
            "running": self._running,
            "events_emitted": self._events_emitted,
            "last_event_timestamp": self._last_timestamp,
            "error": self._error,
        }

    def _emit(self, event: ContextEvent) -> None:
        """Emit an event via the registered callback. Swallows errors."""
        if not self._callback or not self._running:
            return
        try:
            self._callback(event)
            self._events_emitted += 1
            self._last_timestamp = event.timestamp
        except Exception as exc:
            self._error = str(exc)
            logger.exception("SightModule emit error: %s", exc)

    def emit_capture(
        self,
        timestamp: float,
        app_name: str,
        window_title: str,
        monitor_index: int,
        frame_path: str,
        diff_score: float,
        token_estimate: int = 0,
        storage_mode: str = "image",
    ) -> None:
        """Emit a SCREEN_CAPTURE event after a frame is captured."""
        self._emit(ContextEvent(
            timestamp=timestamp,
            modality=Modality.SIGHT,
            event_type=EventType.SCREEN_CAPTURE,
            app_name=app_name,
            window_title=window_title,
            monitor_index=monitor_index,
            payload={
                "frame_path": frame_path,
                "diff_score": diff_score,
                "token_estimate": token_estimate,
                "storage_mode": storage_mode,
            },
        ))

    def emit_ocr(
        self,
        timestamp: float,
        frame_path: str,
        ocr_text: str,
        confidence: float,
        app_name: str = "",
        window_title: str = "",
    ) -> None:
        """Emit an OCR_RESULT event after OCR processing completes."""
        self._emit(ContextEvent(
            timestamp=timestamp,
            modality=Modality.SIGHT,
            event_type=EventType.OCR_RESULT,
            app_name=app_name,
            window_title=window_title,
            payload={
                "ocr_text": ocr_text,
                "ocr_confidence": confidence,
                "frame_path": frame_path,
            },
        ))

    def emit_clipboard(
        self,
        timestamp: float,
        text: str,
        hash_val: str,
        source_app: str | None = None,
    ) -> None:
        """Emit a CLIPBOARD_CHANGE event when clipboard content changes."""
        self._emit(ContextEvent(
            timestamp=timestamp,
            modality=Modality.CLIPBOARD,
            event_type=EventType.CLIPBOARD_CHANGE,
            app_name=source_app or "",
            payload={
                "text": text,
                "hash": hash_val,
                "source_app": source_app,
            },
        ))

    def emit_window_focus(
        self,
        app_name: str,
        window_title: str,
    ) -> None:
        """Emit a WINDOW_FOCUS event when the active window changes."""
        self._emit(ContextEvent(
            modality=Modality.SYSTEM,
            event_type=EventType.WINDOW_FOCUS,
            app_name=app_name,
            window_title=window_title,
        ))

    def emit_idle(self, idle_start: bool) -> None:
        """Emit IDLE_START or IDLE_END event."""
        event_type = EventType.IDLE_START if idle_start else EventType.IDLE_END
        self._emit(ContextEvent(
            modality=Modality.SYSTEM,
            event_type=event_type,
        ))

    def emit_session_lock(self, locked: bool) -> None:
        """Emit SESSION_LOCK or SESSION_UNLOCK event."""
        event_type = EventType.SESSION_LOCK if locked else EventType.SESSION_UNLOCK
        self._emit(ContextEvent(
            modality=Modality.SYSTEM,
            event_type=event_type,
        ))
