# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""The OCR thread cap must reach onnxruntime, not merely be written down.

Background (2026-09-22). ``_thread_caps.apply_caps`` bounds four pools via
``OMP_NUM_THREADS`` and friends, and the daemon has read as fully bounded since
2026-04-29. onnxruntime's CPU wheel has not been an OpenMP build since 1.10, so
it honours none of them; RapidOCR builds a bare ``SessionOptions()`` and leaves
``intra_op_num_threads`` at ORT's default of 0 -- one thread per physical core,
16 on the dev machine, across three sessions. Measured penalty to a concurrent
dictation: +80.1%. Capped at 4: +21.8%, with byte-identical OCR output.

These tests assert the cap by its EFFECT -- the value that arrives at
``InferenceSession`` -- because the mechanism is a scoped swap of a vendor class.
A test that merely confirmed the swap happened would keep passing after a
RapidOCR upgrade that stopped calling ``SessionOptions``, which is exactly the
failure mode worth catching.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from contextpulse_core._thread_caps import get_ocr_cap

# macOS takes the VisionOCR branch in _get_ocr() and never builds an ONNX
# session, so these assertions are meaningless there. Declared in the file
# rather than by leaving macOS off the CI matrix: the matrix currently runs
# tests/ on ubuntu and windows only, and a file that has a platform
# requirement should say so itself instead of depending on a workflow line
# nobody will remember when macOS is added.
pytestmark = pytest.mark.skipif(
    sys.platform == "darwin", reason="macOS uses VisionOCR, not rapidocr/onnxruntime"
)

pytest.importorskip(
    "rapidocr_onnxruntime",
    reason="rapidocr_onnxruntime is the non-macOS OCR backend",
)

# The module whose globals OrtInferSession resolves SessionOptions and
# InferenceSession from. It moved between vendor releases -- 1.2.x keeps them
# in rapidocr_onnxruntime.utils, 1.4.x in rapidocr_onnxruntime.utils.infer_engine
# -- and CI installs both: Python 3.12 resolves 1.4.4, 3.13 resolves 1.2.3.
# Spying on a hard-coded path tested one release and crashed on the other.
from contextpulse_sight.classifier import _vendor_session_module  # noqa: E402

try:
    rapidocr_utils = _vendor_session_module()
except ImportError:
    # packages/screen/tests/conftest.py replaces rapidocr_onnxruntime with a
    # MagicMock in sys.modules, so a bare `pytest` (which collects both trees)
    # sees a "package" with no submodules. Skip rather than fail collection
    # for every test in the run; CI and scripts/ci-tests.sh run tests/ alone.
    pytest.skip(
        "rapidocr_onnxruntime is stubbed in this session (screen conftest)",
        allow_module_level=True,
    )


@pytest.fixture
def fresh_classifier(monkeypatch: pytest.MonkeyPatch):
    """A classifier module with its lazily-built OCR singleton cleared.

    ``_get_ocr`` caches in a module global, so a prior test (or a prior import
    anywhere in the session) would otherwise make this a no-op that passes.
    """
    from contextpulse_sight import classifier

    monkeypatch.setattr(classifier, "_ocr", None)
    return classifier


class TestCapReachesOnnxRuntime:
    def test_every_session_is_built_with_the_configured_cap(
        self, fresh_classifier, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The value arrives at all three ORT sessions (det, cls, rec).

        This constructs the real RapidOCR against the real vendor package. It is
        the only check that survives a vendor upgrade: if RapidOCR stops routing
        through ``SessionOptions``, ``observed`` fills with 0 and this fails.
        """
        observed: list[Any] = []
        real_session = rapidocr_utils.InferenceSession

        def spy(*args: Any, **kwargs: Any):
            opts = kwargs.get("sess_options")
            observed.append(getattr(opts, "intra_op_num_threads", None))
            return real_session(*args, **kwargs)

        monkeypatch.setattr(rapidocr_utils, "InferenceSession", spy)

        fresh_classifier._get_ocr()

        assert observed, "RapidOCR built no ONNX session -- the spy never fired"
        assert set(observed) == {get_ocr_cap()}, (
            f"expected every session at intra_op_num_threads={get_ocr_cap()}, "
            f"got {observed}. 0 means ORT's default (one thread per physical "
            f"core) is back and the cap is inert."
        )

    def test_override_reaches_the_sessions_too(
        self, fresh_classifier, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONTEXTPULSE_OCR_THREADS is honoured end to end, not just by the getter.

        A getter-only test would pass against a classifier that read the cap
        once at import and ignored the environment thereafter.
        """
        monkeypatch.setenv("CONTEXTPULSE_OCR_THREADS", "3")
        observed: list[Any] = []
        real_session = rapidocr_utils.InferenceSession

        def spy(*args: Any, **kwargs: Any):
            observed.append(
                getattr(kwargs.get("sess_options"), "intra_op_num_threads", None)
            )
            return real_session(*args, **kwargs)

        monkeypatch.setattr(rapidocr_utils, "InferenceSession", spy)

        fresh_classifier._get_ocr()

        assert set(observed) == {3}, observed


class TestPatchIsContained:
    """What could have BROKEN, not just what was added."""

    def test_vendor_class_is_restored_after_construction(
        self, fresh_classifier
    ) -> None:
        before = rapidocr_utils.SessionOptions
        fresh_classifier._get_ocr()
        assert rapidocr_utils.SessionOptions is before, (
            "the capped subclass leaked into the vendor module -- every other "
            "onnxruntime consumer in this process would inherit the OCR cap"
        )

    def test_context_manager_restores_on_exception(self) -> None:
        from contextpulse_sight.classifier import _capped_session_options

        before = rapidocr_utils.SessionOptions
        with pytest.raises(RuntimeError):
            with _capped_session_options(4):
                raise RuntimeError("model load failed")
        assert rapidocr_utils.SessionOptions is before

    def test_cap_does_not_move_the_idle_pool_vars(self) -> None:
        """The OCR budget must not leak into OMP/MKL/OPENBLAS/NUMEXPR.

        Those are capped at 2 because of the 2026-04-29 163-thread incident.
        Raising OCR to 4 through them would re-inflate every numpy pool too,
        and all the tests above would still pass.
        """
        from contextpulse_core import _thread_caps

        target: dict[str, str] = {}
        _thread_caps.apply_caps(target)
        assert set(target.values()) == {"2"}, target


class TestVendorShapeStillSupportsTheCap:
    """A drift canary. Fails loudly instead of letting the cap go silently inert."""

    def test_vendor_module_still_exposes_session_options(self) -> None:
        assert hasattr(rapidocr_utils, "SessionOptions")

    def test_vendor_still_builds_session_options_itself(self) -> None:
        """Whole class, not ``__init__``: 1.4.x moved the call into a helper."""
        import inspect

        src = inspect.getsource(rapidocr_utils.OrtInferSession)
        assert "SessionOptions()" in src, (
            "OrtInferSession no longer constructs SessionOptions() -- the swap "
            "in classifier._capped_session_options can no longer reach it"
        )

    def test_the_resolved_module_is_where_the_vendor_looks_names_up(self) -> None:
        """The swap only works if it lands in the namespace OrtInferSession reads.

        Patching any other module that happens to import SessionOptions would
        succeed, restore cleanly, and change nothing.
        """
        init_globals = rapidocr_utils.OrtInferSession.__init__.__globals__
        assert init_globals is vars(rapidocr_utils)
        assert init_globals["SessionOptions"] is rapidocr_utils.SessionOptions

    @pytest.mark.skipif(
        sys.platform == "darwin", reason="macOS uses VisionOCR, not rapidocr"
    )
    def test_the_capped_path_is_the_one_this_platform_takes(self) -> None:
        assert sys.platform != "darwin"
