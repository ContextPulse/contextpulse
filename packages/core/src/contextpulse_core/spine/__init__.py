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
