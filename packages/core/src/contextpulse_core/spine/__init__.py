# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""ContextPulse Spine — Central Memory Engine contract.

The spine defines the universal interfaces for multi-modal context capture:
- ContextEvent: the event format all modalities emit
- EventBus: routes events to storage and listeners
- ModalityModule: abstract base class for capture modules
- Modality/EventType: enums for type-safe event classification
"""

from .bus import EventBus
from .events import ContextEvent, EventType, Modality
from .module import ModalityModule

__all__ = [
    "ContextEvent",
    "EventBus",
    "EventType",
    "Modality",
    "ModalityModule",
]
