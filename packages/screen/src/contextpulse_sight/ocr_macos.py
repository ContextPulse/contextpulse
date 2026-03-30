"""Apple Vision-framework OCR wrapper with the same interface as RapidOCR.

On macOS this is used in place of rapidocr_onnxruntime so that ContextPulse
Sight can leverage the native on-device text recognition engine without
requiring ONNX Runtime or any third-party OCR models.

All PyObjC imports are lazy (inside methods) to allow the module to be
imported on any platform without raising ImportError at the top level.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np


class VisionOCR:
    """Drop-in replacement for ``RapidOCR`` using Apple Vision framework.

    The ``__call__`` interface matches RapidOCR::

        results, elapsed = ocr(image_array)

    where *results* is a list of ``[bbox, text, confidence]`` triples and
    *elapsed* is the wall-clock time in seconds.
    """

    def __call__(
        self, img: np.ndarray, **kwargs: Any
    ) -> tuple[list[list[Any]] | None, float]:
        """Run OCR on a numpy BGR/RGB image array.

        Parameters
        ----------
        img:
            HxWxC uint8 numpy array (BGR or RGB).

        Returns
        -------
        tuple[list | None, float]
            ``(results, elapsed)`` — *results* is ``None`` when no text is
            found, otherwise a list of ``[bbox, text, confidence]``.
        """
        from CoreGraphics import (  # type: ignore[import-not-found]
            CGDataProviderCreateWithData,
            CGImageCreate,
            kCGBitmapByteOrderDefault,
            kCGColorSpaceGenericRGB,
            kCGImageAlphaNoneSkipLast,
            CGColorSpaceCreateWithName,
        )
        from Quartz import CIImage  # type: ignore[import-not-found]
        from Vision import (  # type: ignore[import-not-found]
            VNImageRequestHandler,
            VNRecognizeTextRequest,
        )

        start = time.time()

        h, w = img.shape[:2]
        channels = img.shape[2] if img.ndim == 3 else 1

        # Ensure 4-channel RGBA for CGImage
        if channels == 3:
            rgba = np.empty((h, w, 4), dtype=np.uint8)
            rgba[:, :, :3] = img
            rgba[:, :, 3] = 255
        elif channels == 4:
            rgba = img
        else:
            # Grayscale -> RGBA
            rgba = np.empty((h, w, 4), dtype=np.uint8)
            rgba[:, :, 0] = img if img.ndim == 2 else img[:, :, 0]
            rgba[:, :, 1] = img if img.ndim == 2 else img[:, :, 0]
            rgba[:, :, 2] = img if img.ndim == 2 else img[:, :, 0]
            rgba[:, :, 3] = 255

        bytes_per_row = w * 4
        data = rgba.tobytes()

        color_space = CGColorSpaceCreateWithName(kCGColorSpaceGenericRGB)
        provider = CGDataProviderCreateWithData(None, data, len(data), None)
        cg_image = CGImageCreate(
            w,
            h,
            8,              # bits per component
            32,             # bits per pixel
            bytes_per_row,
            color_space,
            kCGBitmapByteOrderDefault | kCGImageAlphaNoneSkipLast,
            provider,
            None,           # decode array
            False,          # should interpolate
            0,              # rendering intent
        )

        ci_image = CIImage.imageWithCGImage_(cg_image)

        # Build and execute a text-recognition request
        request = VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(1)  # VNRequestTextRecognitionLevelAccurate
        request.setUsesLanguageCorrection_(True)

        handler = VNImageRequestHandler.alloc().initWithCIImage_options_(
            ci_image, None
        )
        success = handler.performRequests_error_([request], None)

        elapsed = time.time() - start

        if not success or not request.results():
            return None, elapsed

        results: list[list[Any]] = []
        for observation in request.results():
            text = observation.topCandidates_(1)[0].string()
            confidence = float(observation.confidence())
            bbox = observation.boundingBox()

            # Vision returns normalised origin-bottom-left; convert to
            # pixel coords as [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] (clockwise
            # from top-left) to match RapidOCR's bbox format.
            x = bbox.origin.x * w
            y = (1 - bbox.origin.y - bbox.size.height) * h
            bw = bbox.size.width * w
            bh = bbox.size.height * h

            box = [
                [x, y],
                [x + bw, y],
                [x + bw, y + bh],
                [x, y + bh],
            ]
            results.append([box, text, confidence])

        if not results:
            return None, elapsed

        return results, elapsed
