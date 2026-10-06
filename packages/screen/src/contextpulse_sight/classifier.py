# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026 Jerard Ventures LLC
"""Screen content classifier: decides whether to send text or image to Claude.

OCR runs on-demand at full native resolution (not on downscaled buffer frames).
When Claude asks for screen context via MCP, we:
1. Capture fresh at native resolution (e.g. 3840x2160)
2. Run OCR on the full-res image
3. If enough text with high confidence -> return text (~200-700 tokens)
4. Otherwise -> return the downscaled image (~1,229 tokens)
"""

import logging
import sys
import threading
import time
from contextlib import contextmanager

import numpy as np
from contextpulse_core._thread_caps import get_ocr_cap
from PIL import Image

logger = logging.getLogger("contextpulse.sight.classifier")

# Lazy-init OCR engine (loads model weights on first call)
_ocr = None
_ocr_lock = threading.Lock()


def _vendor_session_module():
    """Return the RapidOCR module whose globals ``OrtInferSession`` reads.

    ``OrtInferSession`` looks ``SessionOptions`` up in its own module's globals,
    and that module moved between releases: 1.2.x defines it in
    ``rapidocr_onnxruntime.utils`` (a single file), 1.4.x in
    ``rapidocr_onnxruntime.utils.infer_engine`` (``utils`` became a package that
    re-exports only ``OrtInferSession``). Which one a user gets depends on their
    Python: 3.12 resolves 1.4.4, 3.13 resolves 1.2.3. Swapping the class in the
    wrong module succeeds, restores cleanly, and caps nothing.

    Raises ``ImportError`` when RapidOCR is not installed at all.
    """
    import importlib

    try:
        return importlib.import_module("rapidocr_onnxruntime.utils.infer_engine")
    except ModuleNotFoundError as exc:
        # 1.2.x: utils is a module, not a package, so the submodule cannot exist.
        # Re-raise anything else -- a missing rapidocr is the caller's ImportError.
        if exc.name != "rapidocr_onnxruntime.utils.infer_engine":
            raise
        return importlib.import_module("rapidocr_onnxruntime.utils")


@contextmanager
def _capped_session_options(cap: int):
    """Make every ``SessionOptions`` RapidOCR builds carry ``intra_op_num_threads``.

    RapidOCR 1.2.3 exposes no thread knob (1.4.x has one; the swap covers both): ``OrtInferSession.__init__`` builds a
    bare ``SessionOptions()`` and reads only ``use_cuda`` and ``model_path`` from
    its config, so ORT's default of 0 ("one thread per physical core") applies to
    all three sessions. The daemon's ``OMP_NUM_THREADS`` cap cannot reach them --
    the onnxruntime CPU wheel has not been an OpenMP build since 1.10.

    Swapping the class the vendor module looks up is the smallest intervention
    that reaches all three. Scoped to the construction call and restored in
    ``finally`` so nothing else in the process sees the subclass; the caller
    holds ``_ocr_lock`` throughout, and this function is the module's only user.

    If the vendor module cannot be reached, OCR runs UNCAPPED rather than not at
    all: this is a latency optimisation, not a safety guard, and a daemon whose
    screen capture dies because a thread hint could not be applied is strictly
    worse than a slow one. The degradation is logged at WARNING so it is visible
    in the daemon log instead of silent.

    That leaves the cap able to go quietly inert after a vendor upgrade, so it is
    pinned two ways: tests/test_ocr_thread_cap.py asserts the cap by its EFFECT
    (the value reaching ``InferenceSession``) against the real package, and a
    drift canary there fails if ``OrtInferSession`` stops building
    ``SessionOptions`` itself.
    """
    try:
        vendor = _vendor_session_module()
        original = vendor.SessionOptions
    except (ImportError, AttributeError) as exc:
        logger.warning(
            "OCR thread cap NOT applied (%s): onnxruntime will use one thread "
            "per physical core. Dictation running at the same time will be "
            "slower. See contextpulse_core._thread_caps._DEFAULT_OCR_CAP.",
            exc,
        )
        yield
        return

    class _CappedSessionOptions(original):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.intra_op_num_threads = cap

    vendor.SessionOptions = _CappedSessionOptions
    try:
        yield
    finally:
        vendor.SessionOptions = original


def _get_ocr():
    global _ocr
    if _ocr is None:
        with _ocr_lock:
            if _ocr is None:
                if sys.platform == "darwin":
                    from contextpulse_sight.ocr_macos import VisionOCR
                    _ocr = VisionOCR()
                else:
                    from rapidocr_onnxruntime import RapidOCR

                    cap = get_ocr_cap()
                    logger.info(
                        "Loading RapidOCR (onnxruntime intra_op_num_threads=%d)", cap
                    )
                    with _capped_session_options(cap):
                        _ocr = RapidOCR()
    return _ocr


# Thresholds for "text-heavy" classification
MIN_TEXT_CHARS = 100       # need at least this many chars to prefer text
MIN_AVG_CONFIDENCE = 0.70  # OCR confidence threshold


def classify_and_extract(img: Image.Image) -> dict:
    """Run OCR on a full-resolution image and return the best representation."""
    ocr = _get_ocr()

    arr = np.array(img)
    start = time.time()
    result, _ = ocr(arr)
    ocr_time = time.time() - start

    if not result:
        return {
            "type": "image",
            "text": None,
            "lines": 0,
            "chars": 0,
            "confidence": 0.0,
            "ocr_time": ocr_time,
        }

    lines = len(result)
    chars = sum(len(r[1]) for r in result)
    avg_conf = sum(float(r[2]) for r in result) / lines
    text = "\n".join(r[1] for r in result)

    is_text_heavy = chars >= MIN_TEXT_CHARS and avg_conf >= MIN_AVG_CONFIDENCE

    logger.info(
        "OCR: %d lines, %d chars, conf=%.2f, time=%.2fs -> %s",
        lines, chars, avg_conf, ocr_time,
        "TEXT" if is_text_heavy else "IMAGE",
    )

    return {
        "type": "text" if is_text_heavy else "image",
        "text": text if is_text_heavy else None,
        "lines": lines,
        "chars": chars,
        "confidence": avg_conf,
        "ocr_time": ocr_time,
    }
