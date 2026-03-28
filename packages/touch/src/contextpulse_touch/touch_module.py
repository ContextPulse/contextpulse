# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC

"""TouchModule — ModalityModule for keyboard and mouse input capture.

Emits events with both Modality.KEYS (keyboard) and Modality.FLOW (mouse).
Primary modality is KEYS (returned by get_modality()).

Combines contextpulse-keys and contextpulse-flow into a single module
for simpler deployment and because correction detection needs both.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from contextpulse_core.spine import (
    ContextEvent,
    EventType,
    Modality,
    ModalityModule,
)

from contextpulse_touch.burst_tracker import BurstTracker
from contextpulse_touch.config import get_touch_config
from contextpulse_touch.correction_detector import CorrectionDetector
from contextpulse_touch.listeners import KeyboardListener, MouseListener

logger = logging.getLogger(__name__)


class TouchModule(ModalityModule):
    """Keyboard and mouse input capture for ContextPulse.

    Usage:
        module = TouchModule()
        module.register(event_bus.emit)
        module.start()
        # ... captures keyboard bursts, mouse events, and Voice corrections
        module.stop()
    """

    def __init__(self, db_path=None) -> None:
        self._callback: Callable[[ContextEvent], None] | None = None
        self._running = False
        self._events_emitted = 0
        self._last_timestamp: float | None = None
        self._error: str | None = None

        cfg = get_touch_config()

        # BurstTracker
        self._burst_tracker = BurstTracker(
            burst_timeout=cfg["burst_timeout"],
            min_chars=cfg["min_burst_chars"],
            on_burst=self._on_burst,
        )

        # CorrectionDetector
        self._correction_detector = CorrectionDetector(
            burst_tracker=self._burst_tracker,
            on_correction=self._on_correction,
            watch_seconds=cfg["correction_window"],
            db_path=db_path,
        )

        # Listeners
        self._keyboard_listener = KeyboardListener(
            on_char=self._on_keyboard_char,
            on_paste=self._correction_detector.on_paste_detected,
            on_key_event=self._correction_detector.on_key_event,
        )
        self._mouse_listener = MouseListener(
            on_click=self._on_mouse_click,
            on_scroll=self._on_mouse_scroll,
            on_drag=self._on_mouse_drag,
            on_window_change=self._correction_detector.on_window_change,
            debounce_seconds=cfg["mouse_debounce"],
        )

    def get_modality(self) -> Modality:
        return Modality.KEYS

    def register(self, event_callback: Callable[[ContextEvent], None]) -> None:
        self._callback = event_callback

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._error = None
        self._keyboard_listener.start()
        self._mouse_listener.start()
        logger.info("TouchModule started")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._keyboard_listener.stop()
        self._mouse_listener.stop()
        self._burst_tracker.stop()
        self._correction_detector.stop()
        logger.info("TouchModule stopped")

    def is_alive(self) -> bool:
        return self._running

    def get_status(self) -> dict[str, Any]:
        return {
            "modality": "touch",
            "running": self._running,
            "events_emitted": self._events_emitted,
            "last_event_timestamp": self._last_timestamp,
            "error": self._error,
            "corrections_detected": self._correction_detector.corrections_detected,
            "pastes_detected": self._correction_detector.pastes_detected,
            "watching_correction": self._correction_detector.is_watching,
        }

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "touch_burst_timeout": {
                "type": "number", "default": 1.5,
                "description": "Seconds of silence to end a typing burst",
            },
            "touch_correction_window": {
                "type": "number", "default": 15.0,
                "description": "Seconds after paste to watch for corrections",
            },
            "touch_min_burst_chars": {
                "type": "number", "default": 3,
                "description": "Minimum characters for a burst event",
            },
            "touch_mouse_debounce": {
                "type": "number", "default": 0.1,
                "description": "Seconds between mouse events (debounce)",
            },
        }

    def _emit(self, event: ContextEvent) -> None:
        """Emit event via registered callback."""
        if not self._callback or not self._running:
            return
        try:
            self._callback(event)
            self._events_emitted += 1
            self._last_timestamp = event.timestamp
        except Exception as exc:
            self._error = str(exc)
            logger.exception("TouchModule emit error: %s", exc)

    # ── Keyboard event handlers ──────────────────────────────────────

    def _on_keyboard_char(self, key_char: str | None, is_backspace: bool,
                          is_selection: bool) -> None:
        """Feed keystrokes to BurstTracker."""
        self._burst_tracker.on_key_press(key_char, is_backspace, is_selection)

    def _on_burst(self, burst_data: dict) -> None:
        """Emit TYPING_BURST event from BurstTracker."""
        self._emit(ContextEvent(
            modality=Modality.KEYS,
            event_type=EventType.TYPING_BURST,
            payload=burst_data,
        ))

    def _on_correction(self, correction: dict) -> None:
        """Emit CORRECTION_DETECTED event from CorrectionDetector."""
        self._emit(ContextEvent(
            modality=Modality.KEYS,
            event_type=EventType.CORRECTION_DETECTED,
            payload={
                "original_text": correction.get("original_word", ""),
                "corrected_text": correction.get("corrected_word", ""),
                "correction_text": f"{correction.get('original_word', '')} -> {correction.get('corrected_word', '')}",
                "correction_type": correction.get("correction_type", ""),
                "confidence": correction.get("confidence", 0.0),
                "seconds_after_paste": correction.get("seconds_after_paste", 0.0),
                "paste_event_id": correction.get("paste_event_id", ""),
            },
        ))

    # ── Mouse event handlers ─────────────────────────────────────────

    def _on_mouse_click(self, data: dict) -> None:
        self._emit(ContextEvent(
            modality=Modality.FLOW,
            event_type=EventType.CLICK,
            app_name=data.get("app_name", ""),
            window_title=data.get("window_title", ""),
            payload={
                "x": data["x"], "y": data["y"],
                "button": data["button"],
                "click_type": data.get("click_type", "single"),
            },
        ))

    def _on_mouse_scroll(self, data: dict) -> None:
        self._emit(ContextEvent(
            modality=Modality.FLOW,
            event_type=EventType.SCROLL,
            app_name=data.get("app_name", ""),
            window_title=data.get("window_title", ""),
            payload={
                "x": data["x"], "y": data["y"],
                "dx": data["dx"], "dy": data["dy"],
            },
        ))

    def _on_mouse_drag(self, data: dict) -> None:
        self._emit(ContextEvent(
            modality=Modality.FLOW,
            event_type=EventType.DRAG,
            app_name=data.get("app_name", ""),
            window_title=data.get("window_title", ""),
            payload={
                "start_x": data["start_x"], "start_y": data["start_y"],
                "end_x": data["end_x"], "end_y": data["end_y"],
                "duration_ms": data.get("duration_ms", 0),
            },
        ))
