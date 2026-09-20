# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""ContextPulse Spine — ModalityModule abstract base class.

Every capture module (Sight, Voice, Keys, Flow) implements this interface
to participate in the Central Memory Engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from .events import ContextEvent, Modality


class ModalityModule(ABC):
    """Abstract base class for all capture modalities.

    Lifecycle:
        1. Instantiate the module
        2. Call register(callback) with the EventBus.emit method
        3. Call start() to begin capture
        4. Module emits ContextEvent objects via the registered callback
        5. Call stop() to halt capture
    """

    @abstractmethod
    def get_modality(self) -> Modality:
        """Return the modality this module captures."""

    @abstractmethod
    def register(self, event_callback: Callable[[ContextEvent], None]) -> None:
        """Store the EventBus callback for emitting events.

        Args:
            event_callback: Function to call with each new ContextEvent.
                            Typically EventBus.emit.
        """

    @abstractmethod
    def start(self) -> None:
        """Begin capturing and emitting events."""

    @abstractmethod
    def stop(self) -> None:
        """Stop capturing. Must be safe to call multiple times."""

    @abstractmethod
    def is_alive(self) -> bool:
        """Return True if the module is actively capturing."""

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        """Return module health status.

        Must return a dict with at least:
            - modality: str (the modality name)
            - running: bool
            - events_emitted: int
            - last_event_timestamp: float | None
            - error: str | None
        """

    # There is deliberately no get_config_schema(). It declared itself the
    # source of the settings panel's controls -- "Used by the settings panel
    # to render module-specific controls" -- and no settings panel ever called
    # it, in any module, in the project's life. What it did instead was be a
    # THIRD declaration site for values that _DEFAULTS already owned, and it
    # had already drifted from both of the others: voice said
    # voice_whisper_model default "base" against _DEFAULTS "small", and sight
    # declared `capture_interval` and `diff_threshold`, names that match no
    # config key at all.
    #
    # Wiring it instead of deleting it was considered and rejected: a flat
    # {name: {type, default}} cannot express the per-section help text,
    # enumerated combo values, cross-field restart logic or License section
    # the real dialog needs, so "generate the panel from the schema" means
    # building a UI-generation layer. _DEFAULTS plus the reader guard in
    # tests/test_config_readers.py does the schema's only real job -- one
    # declaration per key -- and can be checked automatically.
    #
    # Removing an ABSTRACT method is backward compatible: a third-party
    # module that still defines get_config_schema keeps working.
