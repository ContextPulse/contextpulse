# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""ContextPulse Spine — Unified event schema for multi-modal context capture.

Every modality (Sight, Voice, Keys, Flow) emits ContextEvent objects through
the EventBus. This is the contract that makes cross-modal correlation possible.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Modality(Enum):
    """Capture modalities. Add new modalities here as they ship."""

    SIGHT = "sight"
    VOICE = "voice"
    CLIPBOARD = "clipboard"
    SYSTEM = "system"
    KEYS = "keys"
    FLOW = "flow"


class EventType(Enum):
    """Event types across all modalities."""

    # Sight
    SCREEN_CAPTURE = "screen_capture"
    OCR_RESULT = "ocr_result"

    # Voice
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    TRANSCRIPTION = "transcription"

    # Clipboard
    CLIPBOARD_CHANGE = "clipboard_change"

    # System
    WINDOW_FOCUS = "window_focus"
    IDLE_START = "idle_start"
    IDLE_END = "idle_end"
    SESSION_LOCK = "session_lock"
    SESSION_UNLOCK = "session_unlock"

    # Keys
    KEYSTROKE = "keystroke"
    TYPING_BURST = "typing_burst"
    TYPING_PAUSE = "typing_pause"
    SHORTCUT = "shortcut"
    PASTE_DETECTED = "paste_detected"
    CORRECTION_DETECTED = "correction_detected"

    # Flow
    CLICK = "click"
    SCROLL = "scroll"
    HOVER_DWELL = "hover_dwell"
    DRAG = "drag"


# Keys for extracting searchable text from payloads
_TEXT_PAYLOAD_KEYS = ("ocr_text", "transcript", "text", "burst_text", "correction_text")


@dataclass(frozen=True, slots=True)
class ContextEvent:
    """Universal event format for the Central Memory Engine.

    Every modality module MUST emit events conforming to this schema.
    The EventBus rejects non-conforming events.
    """

    # Required fields
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    timestamp: float = field(default_factory=time.time)
    modality: Modality = Modality.SYSTEM
    event_type: EventType = EventType.WINDOW_FOCUS

    # Context fields (populated by capture module)
    app_name: str = ""
    window_title: str = ""
    monitor_index: int = 0

    # Modality-specific payload (each modality defines its own structure)
    payload: dict[str, Any] = field(default_factory=dict)

    # Engine-populated fields (defaults until correlation/learning engine exists)
    correlation_id: str | None = None
    attention_score: float = 0.0
    cognitive_load: float = 0.0

    def validate(self) -> bool:
        """Check that required fields are present and valid."""
        if not self.event_id or not isinstance(self.timestamp, float):
            return False
        if self.timestamp <= 0 or self.timestamp > time.time() + 60:
            return False
        if not isinstance(self.modality, Modality):
            return False
        if not isinstance(self.event_type, EventType):
            return False
        return True

    def to_row(self) -> dict[str, Any]:
        """Flatten to dict for SQLite insertion."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "modality": self.modality.value,
            "event_type": self.event_type.value,
            "app_name": self.app_name,
            "window_title": self.window_title,
            "monitor_index": self.monitor_index,
            "payload": json.dumps(self.payload),
            "correlation_id": self.correlation_id,
            "attention_score": self.attention_score,
            "cognitive_load": self.cognitive_load,
        }

    def text_content(self) -> str:
        """Extract searchable text from payload for FTS indexing."""
        parts = []
        for key in _TEXT_PAYLOAD_KEYS:
            val = self.payload.get(key)
            if val:
                parts.append(str(val))
        return " ".join(parts)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ContextEvent:
        """Reconstruct a ContextEvent from a database row."""
        payload = row.get("payload", "{}")
        if isinstance(payload, str):
            payload = json.loads(payload)
        return cls(
            event_id=row["event_id"],
            timestamp=row["timestamp"],
            modality=Modality(row["modality"]),
            event_type=EventType(row["event_type"]),
            app_name=row.get("app_name", ""),
            window_title=row.get("window_title", ""),
            monitor_index=row.get("monitor_index", 0),
            payload=payload,
            correlation_id=row.get("correlation_id"),
            attention_score=row.get("attention_score", 0.0),
            cognitive_load=row.get("cognitive_load", 0.0),
        )
