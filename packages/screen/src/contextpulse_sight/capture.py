# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Screen capture engine with DXcam (fast, Windows) and mss (cross-platform fallback).

Handles multi-monitor, cursor tracking, region crop. DXcam uses the Desktop
Duplication API for 3-5x faster capture on Windows. Falls back to mss when
DXcam is unavailable (Linux, macOS, RDP, import failure).
"""

import logging
import sys
import threading
import time
from io import BytesIO
from pathlib import Path

import mss
import numpy as np
from contextpulse_core.config import _DEFAULTS
from contextpulse_core.config import get as cfg_get
from contextpulse_core.platform import get_platform_provider
from PIL import Image

logger = logging.getLogger(__name__)


def _jpeg_quality() -> int:
    """JPEG quality as configured right now (Settings -> jpeg_quality)."""
    return int(cfg_get("jpeg_quality", _DEFAULTS["jpeg_quality"]))


def _max_size() -> tuple[int, int]:
    """Downscale bounds as configured right now."""
    return (
        int(cfg_get("max_width", _DEFAULTS["max_width"])),
        int(cfg_get("max_height", _DEFAULTS["max_height"])),
    )

# --- Backend abstraction ---

_backend: str | None = None  # "dxcam" or "mss", set on first use
_dxcam_cameras: dict[int, object | None] = {}  # output_idx -> camera (None = unavailable)

# A create that fails for any reason other than a missing output (IndexError)
# is retried after this many seconds instead of being cached as None forever.
# The common case is the lock screen (E_ACCESSDENIED): a daemon started while
# locked used to capture with mss for its whole life (2026-10-01: ~23 hours).
_DXCAM_RETRY_S = 120.0
_dxcam_retry_at: dict[int, float] = {}  # output_idx -> time.monotonic() of next attempt

# --- Bounded camera.grab() -------------------------------------------------
#
# Incident 2026-08-24/25: dxcam's Desktop Duplication backend lost the
# display output (screen lock/sleep/monitor reconfig) and its internal
# recovery loop retried for 11+ hours without ever returning from
# camera.grab(). That call is made directly on the auto-capture thread with
# no timeout, so the ENTIRE capture pipeline (screen_capture, OCR, and
# everything downstream of the same thread) silently stalled for the whole
# window -- every liveness check stayed green because the thread was
# alive, just blocked inside a third-party library. A blocking call cannot
# be bounded with except; it needs a real timeout.
#
# Deliberately NOT concurrent.futures.ThreadPoolExecutor: its worker threads
# are non-daemon by design (so queued work isn't dropped at interpreter
# exit), and Python's own shutdown machinery joins every non-daemon thread
# before exiting. Reproduced directly: a plain ThreadPoolExecutor with one
# worker permanently blocked in Event.wait() prevented the whole process
# from exiting at all (still running after 120s+, had to be taskkilled) --
# using it here would trade "capture silently stalls" for "the daemon can
# no longer shut down once a camera wedges." A bare `daemon=True`
# threading.Thread has no such registry and is abandoned for free.
_DXCAM_GRAB_TIMEOUT_SEC = 5.0
_DXCAM_MAX_CONSECUTIVE_TIMEOUTS = 3
_dxcam_timeout_counts: dict[int, int] = {}
# Grab threads abandoned since the last successful grab, per monitor.
_dxcam_grab_workers: dict[int, list[threading.Thread]] = {}
# Evicted cameras, with the grab threads still blocked inside them. Restored
# by _get_dxcam_camera once every one of those threads has returned.
_dxcam_parked: dict[int, tuple[object, list[threading.Thread]]] = {}


def _dxcam_grab_bounded(camera: object, output_idx: int) -> np.ndarray | None:
    """Call ``camera.grab()`` with a hard wall-clock timeout.

    Returns the frame on success, or None on timeout (treated identically
    to dxcam returning None or raising -- caller falls back to mss). A
    genuine exception raised inside grab() re-raises on the calling thread
    unchanged, so existing exception handling at call sites is untouched.

    After ``_DXCAM_MAX_CONSECUTIVE_TIMEOUTS`` consecutive timeouts for a
    given monitor, the cached camera is evicted (mirrors the existing
    None-caching pattern for ``dxcam.create()`` failures) so future
    captures skip DXcam entirely for that monitor instead of repeatedly
    spawning a thread behind an already-stuck grab().

    Note: the daemon thread blocked inside the real ``camera.grab()`` is
    abandoned, not killed -- Python cannot forcibly terminate a thread. This
    unblocks the CALLING thread, which is what keeps the pipeline alive; it
    does not free the stuck worker, and the interpreter can still exit
    around it because the thread is daemon.
    """
    box: dict[str, object] = {}

    def _run() -> None:
        try:
            box["frame"] = camera.grab()
        except BaseException as exc:  # noqa: BLE001 -- re-raised on caller's thread below
            box["exc"] = exc

    worker = threading.Thread(target=_run, daemon=True, name=f"dxcam-grab-{output_idx}")
    worker.start()
    worker.join(timeout=_DXCAM_GRAB_TIMEOUT_SEC)

    if worker.is_alive():
        count = _dxcam_timeout_counts.get(output_idx, 0) + 1
        _dxcam_timeout_counts[output_idx] = count
        _dxcam_grab_workers.setdefault(output_idx, []).append(worker)
        logger.warning(
            "DXcam grab() exceeded %.1fs timeout for monitor %d (consecutive "
            "timeout %d/%d) -- output may be detached (lock/sleep/reconfig); "
            "falling back to mss",
            _DXCAM_GRAB_TIMEOUT_SEC, output_idx, count, _DXCAM_MAX_CONSECUTIVE_TIMEOUTS,
        )
        if count >= _DXCAM_MAX_CONSECUTIVE_TIMEOUTS:
            logger.warning(
                "DXcam monitor %d exceeded %d consecutive grab timeouts -- "
                "parking camera, captures use mss until its blocked grabs return",
                output_idx, _DXCAM_MAX_CONSECUTIVE_TIMEOUTS,
            )
            _dxcam_cameras.pop(output_idx, None)
            _dxcam_parked[output_idx] = (camera, _dxcam_grab_workers.pop(output_idx, []))
            _dxcam_retry_at[output_idx] = time.monotonic() + _DXCAM_RETRY_S
        return None

    _dxcam_timeout_counts[output_idx] = 0
    _dxcam_grab_workers.pop(output_idx, None)
    if "exc" in box:
        raise box["exc"]  # type: ignore[misc]
    return box.get("frame")  # type: ignore[return-value]


def _get_backend() -> str:
    """Detect and cache the best available capture backend.

    Returns "dxcam" on Windows when dxcam is installed, "mss" otherwise.
    """
    global _backend
    if _backend is not None:
        return _backend

    if sys.platform == "win32":
        try:
            import dxcam as _dxcam  # noqa: F401
            _backend = "dxcam"
            logger.info("Using DXcam capture backend (Desktop Duplication API)")
            return _backend
        except (ImportError, OSError):
            pass
        except Exception as exc:
            # dxcam's import-time DXFactory() init talks to the GPU/display
            # adapter directly and can raise anything from that layer --
            # observed live: _ctypes.COMError (a direct Exception subclass,
            # NOT ImportError/OSError -- verified via COMError.__mro__) when
            # Desktop Duplication has no attached output (e.g. the process
            # is stuck in Windows Session 0, which has no desktop). Without
            # this catch, every capture cycle re-attempts `import dxcam`
            # (a failed import is never cached in sys.modules, so it
            # re-executes the module's top-level code) and re-raises the
            # identical traceback forever -- confirmed live: 10,539
            # identical tracebacks, ~20MB of daemon_stderr.log from one
            # process (cp-daemon-stuck-in-session0). Cache the mss fallback
            # exactly like the ImportError/OSError path so this is
            # diagnosed once per process, not every capture cycle.
            #
            # Falling back to mss here must NOT read as an ordinary,
            # quiet recovery: in Windows Session 0 mss does not fail the
            # way dxcam just did -- it returns real-looking bytes for a
            # desktop that does not exist, i.e. plausible garbage, not an
            # error. The daemon's own startup guard
            # (daemon.py:_refuse_if_session_0) is meant to stop the
            # process before it ever reaches this line; this check is
            # defense in depth for any other caller of this module that
            # guard doesn't cover (tests, the MCP server, a future entry
            # point).
            _warn_dxcam_fallback(exc)

    _backend = "mss"
    logger.info("Using mss capture backend (GDI)")
    return _backend


def _warn_dxcam_fallback(exc: Exception) -> None:
    """Log the dxcam->mss fallback, escalated to unmistakable in Session 0."""
    session_id = None
    try:
        from contextpulse_core.session_check import get_windows_session_id
        session_id = get_windows_session_id()
    except Exception:
        logger.debug("Could not determine Windows session id for dxcam diagnostic", exc_info=True)

    if session_id == 0:
        logger.error(
            "DXcam unavailable (%s: %s) AND this process is running in "
            "Windows Session 0 (non-interactive) -- falling back to mss, "
            "but mss captures taken in Session 0 are BLANK/GARBAGE, not a "
            "real screen. This almost certainly means the daemon is "
            "running in the wrong session (see cp-daemon-stuck-in-session0 "
            "/ cp-daemon-session0-blind-capture). Captures from this point "
            "are NOT trustworthy.",
            type(exc).__name__, exc,
        )
    else:
        logger.warning(
            "DXcam unavailable (%s: %s) -- falling back to mss for this process lifetime",
            type(exc).__name__, exc,
        )


# --- Flush after every recovery release ------------------------------------
#
# Incidents 2026-09-26 (17.2 GB) and 2026-09-30 (27.9 GB working set): while
# the desktop is denied to Desktop Duplication (lock screen, HRESULT
# 0x80070005), dxcam 0.3.0's recovery loop retries forever. Each attempt
# releases and rebuilds a full-resolution staging texture and then fails to
# create the duplicator. D3D11 destroys a released resource only when the
# device context is flushed, and that loop submits no GPU work, so every
# released texture stays allocated: 33.2 MB per attempt at 3840x2160, an
# attempt every ~2 s. Measured: +1,995 MB over 60 attempts without a flush,
# one texture's worth with it. Reproduced live 2026-10-01 (592 MB -> 4.69 GB
# private in about two minutes after the display turned off and locked).
#
# dxcam has no public hook for this, so the guard wraps two private
# attributes. If a future dxcam renames them, the guard logs a WARNING and the
# camera is still used; tests/test_dxcam_recovery_flush.py fails on the change.
_RECOVERY_FLUSH_MARK = "_contextpulse_flush_after_release"


def _install_recovery_flush(camera: object) -> bool:
    """Make dxcam's recovery release flush the immediate context afterwards.

    Returns True when the guard is in place (newly or already), False when the
    camera lacks the expected internals. Never raises: a camera without the
    guard still captures, it only keeps the leak.
    """
    recovery = getattr(camera, "_display_recovery", None)
    device = getattr(camera, "_device", None)
    release = getattr(recovery, "_release_resources", None)
    context = getattr(device, "im_context", None)
    if release is None or context is None or not hasattr(context, "Flush"):
        logger.warning(
            "dxcam recovery-leak guard NOT installed: camera %s lacks "
            "_display_recovery._release_resources or _device.im_context.Flush "
            "(dxcam internals changed?) -- a long display loss can grow memory "
            "by one staging texture per recovery attempt",
            type(camera).__name__,
        )
        return False
    if getattr(release, _RECOVERY_FLUSH_MARK, False):
        return True

    try:
        from comtypes import COMError as _com_error  # dxcam depends on comtypes
    except ImportError:  # only reachable with a non-dxcam camera object (tests, other OSes)
        _com_error = OSError

    def _release_then_flush() -> None:
        release()
        try:
            context.Flush()
        except (OSError, _com_error) as exc:
            logger.warning(
                "dxcam recovery: Flush after release failed (%s); released "
                "textures may stay allocated until the next flush",
                exc,
            )

    setattr(_release_then_flush, _RECOVERY_FLUSH_MARK, True)
    recovery._release_resources = _release_then_flush
    logger.info("dxcam recovery-leak guard installed (flush after each recovery release)")
    return True


def _flush_after_failed_create(dxcam_module: object, device_idx: int = 0) -> bool:
    """Flush the device a failed dxcam.create() used, so its texture is freed.

    DXCamera builds a full-resolution staging texture before it creates the
    duplicator, and releases it when the duplicator fails. D3D11 destroys a
    released resource only when the context is flushed, so without this every
    retried create would keep one texture (~33 MB at 4K). Returns False when
    the flush cannot be reached; the caller must then stop retrying.
    """
    factory = vars(dxcam_module).get("__factory")
    try:
        context = factory.devices[device_idx].im_context
        flush = context.Flush
    except (AttributeError, IndexError, TypeError):
        logger.warning(
            "dxcam internals changed: cannot flush after a failed create -- not "
            "retrying, a retry would keep one staging texture per attempt"
        )
        return False

    try:
        from comtypes import COMError as _com_error  # dxcam depends on comtypes
    except ImportError:  # only reachable with a non-dxcam module (tests, other OSes)
        _com_error = OSError
    try:
        flush()
    except (OSError, _com_error) as exc:
        logger.warning("dxcam: Flush after a failed create failed (%s) -- not retrying", exc)
        return False
    return True


def _get_dxcam_camera(output_idx: int = 0) -> object | None:
    """Get or create a DXcam camera for the given monitor output index.

    Returns None when dxcam cannot create a camera for this output, and mss
    handles the capture. An output beyond the adapter's list (IndexError) is
    cached as None for good. Any other failure, typically the lock screen's
    E_ACCESSDENIED, is retried every _DXCAM_RETRY_S seconds, flushing the
    device after each failure so a retry cannot leak a staging texture.
    """
    if output_idx in _dxcam_cameras:
        return _dxcam_cameras[output_idx]
    now = time.monotonic()
    if now < _dxcam_retry_at.get(output_idx, now):
        return None

    if output_idx in _dxcam_parked:
        camera, workers = _dxcam_parked[output_idx]
        if any(w.is_alive() for w in workers):
            _dxcam_retry_at[output_idx] = now + _DXCAM_RETRY_S
            return None
        del _dxcam_parked[output_idx]
        _dxcam_retry_at.pop(output_idx, None)
        if not getattr(camera, "is_released", False):
            # Recreating would not help: dxcam's registry returns this same
            # instance while it is alive and unreleased.
            _dxcam_timeout_counts[output_idx] = 0
            _dxcam_cameras[output_idx] = camera
            logger.info("DXcam monitor %d: blocked grabs returned, camera restored", output_idx)
            return camera
        logger.info("DXcam monitor %d: parked camera was released, recreating", output_idx)

    import dxcam
    try:
        camera = dxcam.create(output_idx=output_idx, output_color="BGR")
    except IndexError as exc:
        logger.info(
            "DXcam output_idx=%d unavailable (%s) — caching None, mss will handle",
            output_idx, exc,
        )
        _dxcam_cameras[output_idx] = None
        return None
    except Exception as exc:  # noqa: BLE001 -- dxcam raises COMError, RuntimeError, OSError
        if not _flush_after_failed_create(dxcam):
            logger.info(
                "DXcam output_idx=%d unavailable (%s) — caching None, mss will handle",
                output_idx, exc,
            )
            _dxcam_cameras[output_idx] = None
            return None
        log = logger.debug if output_idx in _dxcam_retry_at else logger.info
        log(
            "DXcam output_idx=%d unavailable (%s) — mss until retry in %.0f s",
            output_idx, exc, _DXCAM_RETRY_S,
        )
        _dxcam_retry_at[output_idx] = now + _DXCAM_RETRY_S
        return None

    _dxcam_retry_at.pop(output_idx, None)
    try:
        _install_recovery_flush(camera)
    except Exception:  # noqa: BLE001 -- the guard must never cost us the camera
        logger.warning(
            "dxcam recovery-leak guard raised while installing; camera kept without it",
            exc_info=True,
        )
    _dxcam_cameras[output_idx] = camera
    return camera


def _dxcam_to_pil(frame: np.ndarray) -> Image.Image:
    """Convert a DXcam BGR/BGRA numpy array to a PIL RGB Image."""
    if frame.ndim == 3 and frame.shape[2] == 4:
        # BGRA -> RGB
        return Image.fromarray(frame[:, :, 2::-1])
    # BGR -> RGB
    return Image.fromarray(frame[:, :, ::-1])


def _get_cursor_pos() -> tuple[int, int]:
    """Get current cursor position (screen coordinates) via platform provider."""
    return get_platform_provider().get_cursor_pos()


def find_monitor_at_cursor(sct: mss.mss) -> tuple[int, dict]:
    """Return (index, monitor_dict) for the monitor containing the cursor.

    Index is 0-based across physical monitors (sct.monitors[1:]).
    Falls back to primary monitor (index 0).
    """
    cx, cy = _get_cursor_pos()
    for i, mon in enumerate(sct.monitors[1:]):
        if (mon["left"] <= cx < mon["left"] + mon["width"]
                and mon["top"] <= cy < mon["top"] + mon["height"]):
            return i, mon
    # monitors[0] is the virtual desktop; monitors[1:] are physical monitors
    if len(sct.monitors) > 1:
        return 0, sct.monitors[1]
    return 0, sct.monitors[0]


def _downscale(img: Image.Image) -> Image.Image:
    """Downscale image to fit the configured max width/height, keeping aspect."""
    max_width, max_height = _max_size()
    if img.width <= max_width and img.height <= max_height:
        return img
    img.thumbnail((max_width, max_height), Image.LANCZOS)
    return img


def mss_to_pil(sct_img) -> Image.Image:
    """Convert mss screenshot to PIL Image (RGB)."""
    return Image.frombytes("RGB", (sct_img.width, sct_img.height), sct_img.rgb)


def get_monitor_count() -> int:
    """Return the number of physical monitors."""
    with mss.mss() as sct:
        return max(1, len(sct.monitors) - 1)


def capture_active_monitor() -> tuple[int, Image.Image]:
    """Capture the monitor where the cursor currently is, downscaled.

    Returns (monitor_index, image).
    Uses DXcam on Windows for speed, falls back to mss.
    """
    # Always use mss to find which monitor the cursor is on
    with mss.mss() as sct:
        idx, mon = find_monitor_at_cursor(sct)

    if _get_backend() == "dxcam":
        camera = _get_dxcam_camera(idx)
        if camera is not None:
            try:
                frame = _dxcam_grab_bounded(camera, idx)
                if frame is not None:
                    return idx, _downscale(_dxcam_to_pil(frame))
                logger.debug("DXcam returned None for monitor %d, falling back to mss", idx)
            except Exception as exc:
                logger.warning("DXcam capture failed for monitor %d: %s — falling back to mss", idx, exc)

    # Fallback to mss
    with mss.mss() as sct:
        _, mon = find_monitor_at_cursor(sct)
        sct_img = sct.grab(mon)
        img = mss_to_pil(sct_img)
    return idx, _downscale(img)


def capture_single_monitor(index: int) -> Image.Image:
    """Capture a specific monitor by index (0-based), downscaled."""
    # Validate index via mss (it knows how many monitors exist)
    with mss.mss() as sct:
        physical = sct.monitors[1:]
        if index < 0 or index >= len(physical):
            raise ValueError(
                f"Monitor index {index} out of range (0-{len(physical) - 1})"
            )

    if _get_backend() == "dxcam":
        camera = _get_dxcam_camera(index)
        if camera is not None:
            try:
                frame = _dxcam_grab_bounded(camera, index)
                if frame is not None:
                    return _downscale(_dxcam_to_pil(frame))
                logger.debug("DXcam returned None for monitor %d, falling back to mss", index)
            except Exception as exc:
                logger.warning("DXcam capture failed for monitor %d: %s — falling back to mss", index, exc)

    # Fallback to mss
    with mss.mss() as sct:
        sct_img = sct.grab(sct.monitors[1:][index])
        img = mss_to_pil(sct_img)
    return _downscale(img)


def capture_all_monitors(
    keep_native: bool = False,
) -> list[tuple[int, Image.Image]] | list[tuple[int, Image.Image, Image.Image]]:
    """Capture each monitor individually, downscaled.

    Returns list of (monitor_index, downscaled_image) pairs.
    If keep_native=True, returns (monitor_index, downscaled_image, native_image)
    so the caller can pass the native-res image to OCR for better accuracy.

    Uses DXcam per-monitor when available, falls back to mss per-monitor.
    Handles errors per-monitor so a single failure doesn't prevent capturing others.
    """
    with mss.mss() as sct:
        physical = sct.monitors[1:]
        monitor_count = len(physical)

    use_dxcam = _get_backend() == "dxcam"
    results = []

    for i in range(monitor_count):
        try:
            native = None
            if use_dxcam:
                camera = _get_dxcam_camera(i)
                if camera is not None:
                    try:
                        frame = _dxcam_grab_bounded(camera, i)
                        if frame is not None:
                            native = _dxcam_to_pil(frame)
                            scaled = _downscale(native.copy())
                            if keep_native:
                                results.append((i, scaled, native))
                            else:
                                results.append((i, scaled))
                            continue
                        logger.debug("DXcam returned None for monitor %d, trying mss", i)
                    except Exception as exc:
                        logger.warning("DXcam failed for monitor %d: %s — trying mss", i, exc)

            # mss fallback for this monitor
            with mss.mss() as sct:
                mon = sct.monitors[1:][i]
                sct_img = sct.grab(mon)
                native = mss_to_pil(sct_img)
                scaled = _downscale(native.copy())
                if keep_native:
                    results.append((i, scaled, native))
                else:
                    results.append((i, scaled))
        except MemoryError:
            logger.error("MemoryError capturing monitor %d — skipping", i)
            import gc
            gc.collect()
        except Exception as exc:
            logger.error("Failed to capture monitor %d: %s — skipping", i, exc)

    return results


def get_active_window_rect() -> tuple[int, int, int, int] | None:
    """Get the visible bounds of the foreground window in physical pixels.

    Returns (left, top, width, height) or None if no foreground window.
    Uses DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) on Windows
    for accurate bounds without invisible resize borders.
    """
    if sys.platform != "win32":
        return None

    try:
        import ctypes
        import ctypes.wintypes as wintypes

        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi

        hwnd = user32.GetForegroundWindow()
        if not hwnd or not user32.IsWindow(hwnd):
            return None

        # Use DWM for visible bounds (no invisible borders)
        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        hr = dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect), ctypes.sizeof(rect),
        )
        if hr != 0:
            # DWM failed — fall back to GetWindowRect
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            rect.left, rect.top, rect.right, rect.bottom = r.left, r.top, r.right, r.bottom

        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return None

        return (rect.left, rect.top, w, h)
    except Exception as exc:
        logger.debug("get_active_window_rect failed: %s", exc)
        return None


_REGION_PADDING = 50  # pixels around the active window


def capture_region(width: int = 0, height: int = 0) -> Image.Image:
    """Capture a focused region of the screen, downscaled.

    If width and height are 0 (default), auto-sizes to the active window
    bounds plus padding. Falls back to 800x600 cursor-centered if no
    active window is detected.

    Args:
        width: Explicit region width (0 = auto-detect from active window).
        height: Explicit region height (0 = auto-detect from active window).
    """
    # Auto-detect from active window if no explicit size given
    if width == 0 and height == 0:
        win_rect = get_active_window_rect()
        if win_rect is not None:
            wl, wt, ww, wh = win_rect
            # Add padding around the window
            pad = _REGION_PADDING
            with mss.mss() as sct:
                desktop = sct.monitors[0]
                left = max(desktop["left"], wl - pad)
                top = max(desktop["top"], wt - pad)
                right = min(desktop["left"] + desktop["width"], wl + ww + pad)
                bottom = min(desktop["top"] + desktop["height"], wt + wh + pad)
                region = {
                    "left": left, "top": top,
                    "width": right - left, "height": bottom - top,
                }
                sct_img = sct.grab(region)
                img = mss_to_pil(sct_img)
            return _downscale(img)
        # No active window — fall back to default
        width, height = 800, 600

    cx, cy = _get_cursor_pos()
    with mss.mss() as sct:
        desktop = sct.monitors[0]
        left = max(desktop["left"], cx - width // 2)
        top = max(desktop["top"], cy - height // 2)
        right = min(desktop["left"] + desktop["width"], left + width)
        bottom = min(desktop["top"] + desktop["height"], top + height)

        region = {
            "left": left,
            "top": top,
            "width": right - left,
            "height": bottom - top,
        }
        sct_img = sct.grab(region)
        img = mss_to_pil(sct_img)
    return _downscale(img)


def save_image(img: Image.Image, path: Path, fmt: str = "PNG") -> None:
    """Save image to disk. PNG for lossless, JPEG for smaller size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt.upper() == "JPEG":
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(path, format="JPEG", quality=_jpeg_quality())
    else:
        img.save(path, format="PNG")
    logger.info("Saved %s (%dx%d) to %s", fmt, img.width, img.height, path)


def capture_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    """Convert image to bytes (for MCP server)."""
    buf = BytesIO()
    if fmt.upper() == "JPEG" and img.mode == "RGBA":
        img = img.convert("RGB")
    img.save(buf, format=fmt, quality=_jpeg_quality() if fmt.upper() == "JPEG" else None)
    return buf.getvalue()
