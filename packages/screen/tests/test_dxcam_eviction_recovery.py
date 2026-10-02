# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""A dxcam camera evicted after grab timeouts must come back once it unsticks.

After 3 consecutive grab timeouts (the desktop locked while the daemon was
running), _dxcam_grab_bounded used to set the camera to None "until process
restart", so a daemon that ran for days captured with mss after its first
nightly lock. Found by the independent review of 6cfba81, 2026-10-02.

The evicted camera is not recreated: the grab threads still blocked inside it
hold it, and dxcam.create() would hand that same stuck instance back from its
registry. Instead it is parked with its blocked grab threads, and restored once
every one of them has returned (dxcam's own recovery loop finishes when the
display comes back). A camera dxcam has released is dropped and recreated.
"""

import sys
import threading

import pytest
from contextpulse_sight import capture as cap


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _BlockingCamera:
    """grab() blocks until release_grabs() is called, as dxcam's recovery loop does."""

    def __init__(self) -> None:
        self.unblock = threading.Event()
        self.is_released = False

    def grab(self):
        self.unblock.wait()
        return None


@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(cap, "_dxcam_cameras", {})
    monkeypatch.setattr(cap, "_dxcam_retry_at", {})
    monkeypatch.setattr(cap, "_dxcam_timeout_counts", {})
    monkeypatch.setattr(cap, "_dxcam_parked", {})
    monkeypatch.setattr(cap, "_dxcam_grab_workers", {})
    monkeypatch.setattr(cap, "_DXCAM_GRAB_TIMEOUT_SEC", 0.05)
    clock = _Clock()
    monkeypatch.setattr(cap.time, "monotonic", clock)
    creates: list[int] = []
    module = type(sys)("dxcam")

    def create(output_idx: int, output_color: str):
        creates.append(output_idx)
        return object()

    module.create = create
    monkeypatch.setitem(sys.modules, "dxcam", module)
    return clock, creates


def _evict(camera: _BlockingCamera, idx: int = 0) -> None:
    cap._dxcam_cameras[idx] = camera
    for _ in range(cap._DXCAM_MAX_CONSECUTIVE_TIMEOUTS):
        assert cap._dxcam_grab_bounded(camera, idx) is None


def _join_grabs(idx: int = 0) -> None:
    for worker in cap._dxcam_parked[idx][1]:
        worker.join(2)


def test_eviction_parks_the_camera_and_falls_back_to_mss(state):
    clock, creates = state
    camera = _BlockingCamera()
    _evict(camera)
    assert cap._get_dxcam_camera(0) is None
    assert creates == [], "an evicted camera must not be recreated: dxcam returns the stuck instance"
    camera.unblock.set()


def test_still_blocked_camera_stays_parked_after_the_window(state):
    clock, creates = state
    camera = _BlockingCamera()
    _evict(camera)
    clock.now += cap._DXCAM_RETRY_S + 1
    assert cap._get_dxcam_camera(0) is None
    assert creates == []
    camera.unblock.set()


def test_camera_is_restored_once_every_blocked_grab_returns(state):
    clock, creates = state
    camera = _BlockingCamera()
    _evict(camera)
    assert len(cap._dxcam_parked[0][1]) == cap._DXCAM_MAX_CONSECUTIVE_TIMEOUTS
    camera.unblock.set()
    _join_grabs()

    assert cap._get_dxcam_camera(0) is None, "restored before the retry window passed"
    clock.now += cap._DXCAM_RETRY_S + 1
    assert cap._get_dxcam_camera(0) is camera
    assert creates == []
    assert 0 not in cap._dxcam_parked
    assert cap._dxcam_timeout_counts.get(0, 0) == 0


def test_released_camera_is_dropped_and_recreated(state):
    clock, creates = state
    camera = _BlockingCamera()
    _evict(camera)
    camera.is_released = True
    camera.unblock.set()
    _join_grabs()
    clock.now += cap._DXCAM_RETRY_S + 1
    fresh = cap._get_dxcam_camera(0)
    assert fresh is not None and fresh is not camera
    assert creates == [0]


def test_successful_grab_clears_the_tracked_workers(state):
    class _Fast:
        def grab(self):
            return "frame"

    cap._dxcam_grab_workers[0] = ["stale"]
    assert cap._dxcam_grab_bounded(_Fast(), 0) == "frame"
    assert cap._dxcam_grab_workers.get(0, []) == []
