# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""A dxcam camera that failed to start on the lock screen must be retried.

Measured 2026-10-02: the daemon started at 2026-10-01 04:46:55 while the
desktop was locked. dxcam.create() raised COMError E_ACCESSDENIED, the None
was cached, and the daemon captured with mss for the rest of its life (about
23 hours), long after the desktop was unlocked.

Retrying is not free. A failed DXCamera construction has already built and
released a full-resolution staging texture, and D3D11 frees a released
resource only on the next context flush (the same mechanism as the recovery
leak fixed in test_dxcam_recovery_flush.py). So every retry must flush, and if
the flush cannot be reached the old cache-forever behaviour is kept.
"""

import gc
import os
import sys

import pytest
from contextpulse_sight import capture as cap


class _Ctx:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def Flush(self) -> None:  # noqa: N802 -- COM method name
        self._calls.append("flush")


class _Dev:
    def __init__(self, calls: list[str]) -> None:
        self.im_context = _Ctx(calls)


class _Factory:
    def __init__(self, calls: list[str]) -> None:
        self.devices = [_Dev(calls)]


class _AccessDenied(Exception):
    """Stands in for comtypes.COMError(-2147024891, 'Access is denied.')."""


def _fake_dxcam(monkeypatch, calls: list[str], outcomes: list, factory: object | None = "default"):
    """Install a fake dxcam whose create() pops outcomes: an exception to raise, or a camera."""
    module = type(sys)("dxcam")

    def create(output_idx: int, output_color: str):
        calls.append("create")
        result = outcomes.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    module.create = create
    if factory == "default":
        factory = _Factory(calls)
    if factory is not None:
        vars(module)["__factory"] = factory
    monkeypatch.setitem(sys.modules, "dxcam", module)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(cap, "_dxcam_cameras", {})
    monkeypatch.setattr(cap, "_dxcam_retry_at", {})
    monkeypatch.setattr(cap, "_dxcam_parked", {})
    monkeypatch.setattr(cap, "_install_recovery_flush", lambda camera: True)
    clock = _Clock()
    monkeypatch.setattr(cap.time, "monotonic", clock)
    return clock


def test_access_denied_is_not_cached_forever(monkeypatch, state):
    calls: list[str] = []
    camera = object()
    _fake_dxcam(monkeypatch, calls, [_AccessDenied("locked"), camera])

    assert cap._get_dxcam_camera(0) is None
    assert 0 not in cap._dxcam_cameras, "a transient failure was cached as permanently unavailable"

    state.now += cap._DXCAM_RETRY_S + 1
    assert cap._get_dxcam_camera(0) is camera
    assert cap._dxcam_cameras[0] is camera
    assert 0 not in cap._dxcam_retry_at


def test_failed_create_is_followed_by_a_flush(monkeypatch, state):
    calls: list[str] = []
    _fake_dxcam(monkeypatch, calls, [_AccessDenied("locked")])
    cap._get_dxcam_camera(0)
    assert calls == ["create", "flush"]


def test_no_retry_inside_the_backoff_window(monkeypatch, state):
    calls: list[str] = []
    _fake_dxcam(monkeypatch, calls, [_AccessDenied("locked")])
    cap._get_dxcam_camera(0)
    state.now += cap._DXCAM_RETRY_S - 1
    assert cap._get_dxcam_camera(0) is None
    assert calls.count("create") == 1


def test_index_error_is_still_cached_forever(monkeypatch, state):
    calls: list[str] = []
    _fake_dxcam(monkeypatch, calls, [IndexError("list index out of range")])
    assert cap._get_dxcam_camera(1) is None
    assert cap._dxcam_cameras == {1: None}
    state.now += 10 * cap._DXCAM_RETRY_S
    assert cap._get_dxcam_camera(1) is None
    assert calls.count("create") == 1


def test_unreachable_flush_falls_back_to_caching_none(monkeypatch, state, caplog):
    """Without a flush a retry would leak one staging texture per attempt."""
    calls: list[str] = []
    _fake_dxcam(monkeypatch, calls, [_AccessDenied("locked")], factory=None)
    assert cap._get_dxcam_camera(0) is None
    assert cap._dxcam_cameras == {0: None}
    assert "cannot flush" in caplog.text


_GPU_OPT_IN = os.environ.get("CONTEXTPULSE_GPU_TESTS") == "1"


@pytest.mark.skipif(sys.platform != "win32", reason="Desktop Duplication is Windows-only")
@pytest.mark.skipif(
    not _GPU_OPT_IN, reason="needs a real GPU and desktop; opt in with CONTEXTPULSE_GPU_TESTS=1"
)
def test_real_failed_creates_do_not_grow_private_memory(monkeypatch):
    """Real dxcam, real device, real staging texture. Only the duplicator is
    stubbed to fail with E_ACCESSDENIED, as the lock screen makes it fail.
    Without the flush each failed create keeps one texture (~33 MB at 4K)."""
    import comtypes
    import psutil
    from dxcam.dxcam import DXCamera

    def denied(self):
        raise comtypes.COMError(-2147024891, "Access is denied.", (None, None, None, 0, None))

    monkeypatch.setattr(cap, "_dxcam_cameras", {})
    monkeypatch.setattr(cap, "_dxcam_retry_at", {})
    monkeypatch.setattr(cap, "_dxcam_parked", {})
    monkeypatch.setattr(cap, "_DXCAM_RETRY_S", 0.0)
    monkeypatch.setattr(DXCamera, "_create_duplicator", denied)

    proc = psutil.Process()
    cap._get_dxcam_camera(0)  # first attempt: warm up dxcam's factory and device
    gc.collect()
    before = proc.memory_info().private
    attempts = 30
    for _ in range(attempts):
        assert cap._get_dxcam_camera(0) is None
    gc.collect()
    grown_mb = (proc.memory_info().private - before) / 1e6

    import ctypes

    width = ctypes.windll.user32.GetSystemMetrics(0)
    height = ctypes.windll.user32.GetSystemMetrics(1)
    texture_mb = width * height * 4 / 1e6
    assert grown_mb < 3 * texture_mb, (
        f"private memory grew {grown_mb:.0f} MB over {attempts} failed creates "
        f"(~{texture_mb:.0f} MB per texture): released staging textures are not being destroyed"
    )
