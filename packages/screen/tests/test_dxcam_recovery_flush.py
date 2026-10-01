# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""The daemon must not leak a staging texture per dxcam recovery attempt.

Incident, 2026-09-26 and 2026-09-30: the daemon reached 17.2 GB and 27.9 GB
working set. dxcam 0.3.0 retries Desktop Duplication recovery forever while the
desktop is denied (lock screen, HRESULT 0x80070005). Each attempt releases and
rebuilds a full-resolution staging texture, then fails to create the
duplicator. D3D11 destroys a released resource only when the device context is
flushed, and that loop submits no GPU work, so every texture stays allocated:
33.2 MB per attempt at 3840x2160, one attempt every ~2 s. Reproduced live on
2026-10-01 (592 MB -> 4.69 GB private in about two minutes).

capture._get_dxcam_camera wraps the camera's recovery release so the immediate
context is flushed after each release. These tests pin that wrapper; the last
one measures the real leak on a real GPU and is opt-in.
"""

import gc
import os
import sys
import threading

import pytest
from contextpulse_sight import capture as cap


class _FakeContext:
    """Stands in for ID3D11DeviceContext: records Flush calls, nothing else."""

    def __init__(self, calls: list[str], raise_on_flush: Exception | None = None) -> None:
        self._calls = calls
        self._raise = raise_on_flush

    def Flush(self) -> None:  # noqa: N802 -- COM method name
        self._calls.append("flush")
        if self._raise is not None:
            raise self._raise


class _FakeDevice:
    def __init__(self, ctx: _FakeContext) -> None:
        self.im_context = ctx


class _FakeRecovery:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls
        self._release_resources = self._release

    def _release(self) -> None:
        self._calls.append("release")


class _FakeCamera:
    """Only the attributes the guard touches. A missing attribute raises, so a
    guard reaching for anything else fails here instead of passing silently."""

    __slots__ = ("_display_recovery", "_device")

    def __init__(self, calls: list[str], raise_on_flush: Exception | None = None) -> None:
        self._display_recovery = _FakeRecovery(calls)
        self._device = _FakeDevice(_FakeContext(calls, raise_on_flush))


@pytest.fixture
def fresh_cameras(monkeypatch):
    monkeypatch.setattr(cap, "_dxcam_cameras", {})


def _install_fake_dxcam(monkeypatch, camera) -> None:
    class _Mod:
        @staticmethod
        def create(output_idx: int, output_color: str):
            return camera

    monkeypatch.setitem(sys.modules, "dxcam", _Mod)


def test_recovery_release_is_followed_by_a_context_flush(monkeypatch, fresh_cameras):
    calls: list[str] = []
    camera = _FakeCamera(calls)
    _install_fake_dxcam(monkeypatch, camera)

    got = cap._get_dxcam_camera(0)
    assert got is camera

    got._display_recovery._release_resources()
    got._display_recovery._release_resources()

    assert calls == ["release", "flush", "release", "flush"]


def test_guard_installs_once_when_applied_twice(monkeypatch):
    calls: list[str] = []
    camera = _FakeCamera(calls)

    assert cap._install_recovery_flush(camera) is True
    assert cap._install_recovery_flush(camera) is True

    camera._display_recovery._release_resources()
    assert calls == ["release", "flush"]


def test_unknown_dxcam_internals_log_a_warning_and_keep_the_camera(
    monkeypatch, fresh_cameras, caplog
):
    class _BareCamera:
        __slots__ = ()

    camera = _BareCamera()
    _install_fake_dxcam(monkeypatch, camera)

    with caplog.at_level("WARNING", logger="contextpulse_sight.capture"):
        got = cap._get_dxcam_camera(0)

    assert got is camera, "a camera without the guard must still be used for capture"
    assert any(
        "recovery" in r.getMessage() and "NOT installed" in r.getMessage() for r in caplog.records
    )


def test_a_guard_that_raises_still_leaves_the_camera_in_use(monkeypatch, fresh_cameras):
    calls: list[str] = []
    camera = _FakeCamera(calls)
    _install_fake_dxcam(monkeypatch, camera)

    def _boom(_camera: object) -> bool:
        raise ValueError("NULL pointer access")

    monkeypatch.setattr(cap, "_install_recovery_flush", _boom)

    assert cap._get_dxcam_camera(0) is camera
    assert cap._dxcam_cameras[0] is camera


def test_dxcam_recovery_handler_calls_the_guarded_release_each_attempt():
    """Runs dxcam's own DisplayRecoveryHandler.handle loop (not a fake loop), so
    a dxcam release that binds the release callable elsewhere fails here, in the
    default run, without a GPU."""
    pytest.importorskip("comtypes")
    dr = pytest.importorskip("dxcam.core.display_recovery")
    import comtypes
    from dxcam.core.output_recovery import OutputState

    calls: list[str] = []
    attempts = {"n": 0}
    parked = threading.Event()

    class _OutputRecovery:
        def handle(self, *, requested_region, region_set_by_user):
            return OutputState(
                width=8, height=8, rotation_angle=0, region=(0, 0, 8, 8), region_was_clamped=False
            )

    def _create_duplicator():
        attempts["n"] += 1
        if attempts["n"] >= 3:
            parked.set()
            threading.Event().wait()  # park; daemon thread
        raise comtypes.COMError(-2147024891, "E_ACCESSDENIED", (None, None, None, 0, None))

    handler = dr.DisplayRecoveryHandler(
        backend="dxgi",
        output_recovery=_OutputRecovery(),
        release_resources=lambda: calls.append("release"),
        rebuild_stage_surface=lambda: calls.append("rebuild"),
        create_duplicator=_create_duplicator,
        rebuild_frame_buffer=lambda region: None,
    )
    handler._wait.next_delay_seconds = lambda: 0.0

    class _Camera:
        __slots__ = ("_display_recovery", "_device")

    camera = _Camera()
    camera._display_recovery = handler
    camera._device = _FakeDevice(_FakeContext(calls))
    assert cap._install_recovery_flush(camera) is True

    threading.Thread(
        target=lambda: handler.handle(
            region=(0, 0, 8, 8), region_set_by_user=False, is_capturing=False
        ),
        daemon=True,
    ).start()
    assert parked.wait(10), "dxcam's recovery loop did not reach the third attempt"

    assert calls[:9] == ["release", "flush", "rebuild"] * 3


def _flush_errors() -> list[Exception]:
    errors: list[Exception] = [OSError("device removed")]
    try:
        import comtypes
    except ImportError:
        return errors
    errors.append(comtypes.COMError(-2005270523, "DXGI_ERROR_DEVICE_REMOVED", (None,) * 4 + (0,)))
    return errors


@pytest.mark.parametrize("error", _flush_errors(), ids=lambda e: type(e).__name__)
def test_a_failing_flush_does_not_break_the_release(caplog, error):
    calls: list[str] = []
    camera = _FakeCamera(calls, raise_on_flush=error)
    assert cap._install_recovery_flush(camera) is True

    with caplog.at_level("WARNING", logger="contextpulse_sight.capture"):
        camera._display_recovery._release_resources()

    assert calls == ["release", "flush"]
    assert any("Flush" in r.getMessage() for r in caplog.records)


_GPU_OPT_IN = os.environ.get("CONTEXTPULSE_GPU_TESTS") == "1"


@pytest.mark.skipif(sys.platform != "win32", reason="Desktop Duplication is Windows-only")
@pytest.mark.skipif(
    not _GPU_OPT_IN, reason="needs a real GPU and desktop; opt in with CONTEXTPULSE_GPU_TESTS=1"
)
def test_real_recovery_loop_does_not_grow_private_memory(fresh_cameras):
    """Real dxcam camera, real device, real stage-surface rebuild. Only the
    duplicator is stubbed: at construction (so the camera builds on a locked
    desktop) and in recovery, where it fails with E_ACCESSDENIED exactly as the
    lock screen makes it fail. Without the guard this grows ~33 MB per attempt
    at 4K (measured: +1,995 MB over 60 attempts)."""
    import comtypes
    import dxcam
    import psutil
    from dxcam.dxcam import DXCamera

    class _StubDup:
        def release(self) -> None:
            pass

    real_create_dup = DXCamera._create_duplicator
    DXCamera._create_duplicator = lambda self: _StubDup()
    try:
        camera = dxcam.create(output_idx=0, output_color="BGR")
    finally:
        DXCamera._create_duplicator = real_create_dup
    assert cap._install_recovery_flush(camera) is True

    texture_mb = camera.width * camera.height * 4 / 1e6
    attempts_target = 30
    attempts = {"n": 0}
    parked = threading.Event()

    def failing_create():
        attempts["n"] += 1
        if attempts["n"] >= attempts_target:
            parked.set()
            threading.Event().wait()  # park the loop thread; it is a daemon thread
        raise comtypes.COMError(-2147024891, "E_ACCESSDENIED", (None, None, None, 0, None))

    rec = camera._display_recovery
    rec._create_duplicator = failing_create
    rec._wait.next_delay_seconds = lambda: 0.01

    proc = psutil.Process()
    gc.collect()
    before = proc.memory_info().private
    threading.Thread(target=camera._recover_output, daemon=True).start()
    assert parked.wait(60), "recovery loop did not reach the target attempt count"
    gc.collect()
    grown_mb = (proc.memory_info().private - before) / 1e6

    # One live texture is expected (the attempt that is parked rebuilt it).
    assert grown_mb < 3 * texture_mb, (
        f"private memory grew {grown_mb:.0f} MB over {attempts['n']} recovery attempts "
        f"({texture_mb:.0f} MB per texture): released staging textures are not being destroyed"
    )
