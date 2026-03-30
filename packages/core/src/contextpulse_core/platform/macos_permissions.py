"""Detect macOS TCC (Transparency, Consent, and Control) permissions.

macOS requires explicit user consent for screen recording, accessibility,
input monitoring, and microphone access. This module checks whether each
permission has been granted by probing the relevant system APIs.

All PyObjC imports are lazy (inside functions) so this file can be safely
imported on any platform without ImportError.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def check_screen_recording() -> bool:
    """Return True if the app has screen-recording permission.

    Attempts a CGWindowListCreateImage call; a non-None result means
    the TCC grant is in place.
    """
    from Quartz import (  # type: ignore[import-not-found]
        CGRectInfinite,
        CGWindowListCreateImage,
        kCGWindowImageDefault,
        kCGWindowListOptionOnScreenOnly,
    )

    image = CGWindowListCreateImage(
        CGRectInfinite,
        kCGWindowListOptionOnScreenOnly,
        0,  # kCGNullWindowID
        kCGWindowImageDefault,
    )
    return image is not None


def check_accessibility() -> bool:
    """Return True if the app has accessibility (AX) permission."""
    from ApplicationServices import AXIsProcessTrusted  # type: ignore[import-not-found]

    return bool(AXIsProcessTrusted())


def check_input_monitoring() -> bool:
    """Return True if the app has input-monitoring permission.

    Creates a passive CGEventTap; if the system returns a non-None tap
    the permission is granted. The tap is immediately invalidated and
    released to avoid side-effects.
    """
    from Quartz import (  # type: ignore[import-not-found]
        CFMachPortInvalidate,
        CFRelease,
        CGEventTapCreate,
        kCGEventTapOptionListenOnly,
        kCGHeadInsertEventTap,
        kCGSessionEventTap,
    )

    mask = 0  # no events — we only care whether the tap succeeds
    tap = CGEventTapCreate(
        kCGSessionEventTap,
        kCGHeadInsertEventTap,
        kCGEventTapOptionListenOnly,
        mask,
        lambda *_args: None,
        None,
    )
    if tap is None:
        return False

    # Clean up immediately
    CFMachPortInvalidate(tap)
    CFRelease(tap)
    return True


def check_microphone() -> bool:
    """Return True if microphone access is authorized."""
    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio  # type: ignore[import-not-found]

    status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
    # AVAuthorizationStatusAuthorized == 3
    return status == 3


# ---------------------------------------------------------------------------
# Aggregate helpers
# ---------------------------------------------------------------------------

# Maps each ContextPulse module to the permissions it requires.
_MODULE_PERMISSIONS: dict[str, list[dict[str, Any]]] = {
    "sight": [
        {
            "name": "Screen Recording",
            "path": "Privacy & Security > Screen Recording",
            "reason": "ContextPulse Sight needs screen-recording access to capture screen content.",
            "check": check_screen_recording,
        },
    ],
    "voice": [
        {
            "name": "Microphone",
            "path": "Privacy & Security > Microphone",
            "reason": "ContextPulse Voice needs microphone access to transcribe speech.",
            "check": check_microphone,
        },
    ],
    "touch": [
        {
            "name": "Accessibility",
            "path": "Privacy & Security > Accessibility",
            "reason": "ContextPulse Touch needs accessibility access to observe UI events.",
            "check": check_accessibility,
        },
        {
            "name": "Input Monitoring",
            "path": "Privacy & Security > Input Monitoring",
            "reason": "ContextPulse Touch needs input-monitoring access to track keyboard/mouse.",
            "check": check_input_monitoring,
        },
    ],
}


def get_missing_permissions(modules: list[str]) -> list[dict]:
    """Check permissions required by *modules* and return those not yet granted.

    Parameters
    ----------
    modules:
        Subset of ``["sight", "voice", "touch"]``.

    Returns
    -------
    list[dict]
        Each dict has keys ``name``, ``path``, and ``reason`` for every
        permission that is currently missing.
    """
    missing: list[dict] = []
    for mod in modules:
        for perm in _MODULE_PERMISSIONS.get(mod, []):
            try:
                granted = perm["check"]()
            except Exception:
                granted = False
            if not granted:
                missing.append(
                    {
                        "name": perm["name"],
                        "path": perm["path"],
                        "reason": perm["reason"],
                    }
                )
    return missing


def open_privacy_settings() -> None:
    """Open System Settings to the Privacy & Security pane."""
    subprocess.run(
        [
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy",
        ],
        check=False,
    )
