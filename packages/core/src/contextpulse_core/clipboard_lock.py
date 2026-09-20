# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Process-wide mutual exclusion for Win32 clipboard access.

Lives in its own module, importing nothing but :mod:`threading`, so that both
the Windows-only platform provider and the cross-platform voice paster can
hold the SAME lock. ``contextpulse_core.platform.windows`` binds
``ctypes.windll`` at module scope and therefore cannot be imported off
Windows; putting the lock there would make the paster unimportable on macOS
and Linux.

Why a lock at all (cp-daemon-heap-corruption-after-paste). The daemon exited
0xC0000374 STATUS_HEAP_CORRUPTION twice, both times within a second of a
dictation paste. ``pyperclip.copy()`` calls EmptyClipboard, which FREES every
handle on the clipboard, while the sight module's 1s poller may be holding a
GlobalLock'd pointer into one of them. The resulting read-after-free and
GlobalUnlock-after-free are heap-metadata writes on a dead block, which
RtlpHeapHandleError reports through ``__fastfail`` -- no exception reaches
Python, which is why the crash leaves no traceback.

**Why a threading lock is sufficient here.** The poller and the paster are
two threads of ONE process: `contextpulse_core.daemon` starts
`ClipboardMonitor` (packages/screen) and the voice module's paste path in the
same interpreter. A threading primitive covers every pair of callers that
actually exists, needs no OS handle, and cannot be orphaned by a crash the
way a named mutex can.

**What it does NOT cover, deliberately.** Nothing outside this process. The
Win32 clipboard is a machine-global resource, and another application calling
EmptyClipboard while this daemon holds a GlobalLock'd pointer is the same
hazard with none of the same remedy available. Serialising across processes
would need a named mutex that every clipboard-touching application on the
desktop agreed to take, which is not a thing that exists; OpenClipboard is
the OS's own attempt at it and does not reliably exclude a second thread of
the SAME process, which is the gap this closes. The cross-process residue is
mitigated instead by bounding the read (see ``get_clipboard_text``) and by
keeping the locked window as short as the API allows.
"""

import os
import threading
import time

__all__ = ["CLIPBOARD_LOCK_TIMEOUT", "clipboard_lock", "write_breadcrumb"]

# Every in-process Win32 clipboard operation -- read OR write -- must hold
# this for the whole open/use/close cycle, not merely for the API call.
# Re-entrant because the paster holds it across a region that may call back
# into clipboard helpers.
clipboard_lock = threading.RLock()

# A clipboard operation that cannot get the lock gives up rather than blocking
# a real-time path forever. The paster holds the lock for ~0.7s per paste; the
# poller only wants a turn and can skip a beat. Chosen above the paster's hold
# time so a legitimate paste never starves the poller into a spurious warning.
CLIPBOARD_LOCK_TIMEOUT = 2.0


def write_breadcrumb(name: str) -> None:
    """Record which native clipboard call is live, crash-survivably.

    cp-daemon-heap-corruption-after-paste: the daemon exits 0xC0000374 with no
    Python traceback, because RtlpHeapHandleError raises it via ``__fastfail``
    -- no exception is delivered to Python, no handler runs, no ``finally``
    executes. The only evidence that survives is bytes already handed to the
    OS.

    So: one ``os.write()`` on fd 2, NOT the logging module. logging takes a
    lock and formats through several Python frames, and a ``__fastfail`` abort
    can land anywhere in that. A single write syscall reaches the OS file
    handle, which outlives the process -- daemon-watchdog.ps1 redirects fd 2
    to daemon_stderr.log.

    Never allowed to raise: an instrument that breaks the thing it measures is
    worse than no instrument. This is not a bare except hiding a fault -- a
    phase marker has no failure mode worth surfacing, and real breakage still
    shows up as a MISSING breadcrumb, which is exactly the signal being
    collected.
    """
    try:
        os.write(2, f"CLIPBOARD_PHASE {time.time():.3f} {name}\n".encode())
    except Exception:  # noqa: BLE001 — see docstring
        pass


def breadcrumbs_enabled(var: str, default: str = "1") -> bool:
    """Read a breadcrumb on/off switch from the environment."""
    return os.environ.get(var, default) != "0"
