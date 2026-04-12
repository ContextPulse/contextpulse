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

import numpy as np
from PIL import Image

logger = logging.getLogger("contextpulse.sight.classifier")

# Lazy-init OCR engine (loads model weights on first call)
_ocr = None
_ocr_lock = threading.Lock()


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
