"""Rolling screenshot buffer with change detection and OCR classification.

Keeps only the last N minutes of captures on disk. Skips frames that are
visually identical to the previous one (< threshold % pixel difference).

Each stored frame has a paired .txt file if OCR detected enough text.
The MCP server uses the .txt when available (cheap) and falls back to
the .jpg (expensive) only for visual content.
"""

import json
import logging
import time
from pathlib import Path

import numpy as np
from PIL import Image

from contextpulse_sight.config import (
    BUFFER_DIR,
    BUFFER_MAX_AGE,
    CHANGE_THRESHOLD,
    JPEG_QUALITY,
)

logger = logging.getLogger("contextpulse.sight.buffer")


class RollingBuffer:
    def __init__(self):
        BUFFER_DIR.mkdir(parents=True, exist_ok=True)
        self._last_frame: np.ndarray | None = None
        self._prune()

    def add(self, img: Image.Image) -> bool:
        """Add a frame if it differs from the last one. Returns True if stored."""
        arr = np.asarray(img)

        if self._last_frame is not None and not self._has_changed(arr):
            return False

        self._last_frame = arr
        ts = int(time.time() * 1000)
        path = BUFFER_DIR / f"{ts}.jpg"
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(path, format="JPEG", quality=JPEG_QUALITY)
        self._prune()
        return True

    def add_ocr_text(self, frame_path: Path, text: str, confidence: float):
        """Store OCR text alongside a frame. Called async after OCR completes."""
        txt_path = frame_path.with_suffix(".txt")
        meta = {
            "text": text,
            "confidence": round(confidence, 2),
            "timestamp": frame_path.stem,
        }
        txt_path.write_text(json.dumps(meta), encoding="utf-8")

    def _has_changed(self, current: np.ndarray) -> bool:
        """Compare current frame to last. Returns True if meaningfully different."""
        if self._last_frame is None:
            return True
        if self._last_frame.shape != current.shape:
            return True

        diff = np.mean(np.abs(current.astype(np.int16) - self._last_frame.astype(np.int16)))
        pct = diff / 255.0 * 100.0
        return pct >= CHANGE_THRESHOLD

    def _prune(self):
        """Remove frames older than BUFFER_MAX_AGE seconds."""
        cutoff = (time.time() - BUFFER_MAX_AGE) * 1000
        for f in BUFFER_DIR.glob("*.jpg"):
            try:
                ts = int(f.stem)
                if ts < cutoff:
                    f.unlink()
                    txt = f.with_suffix(".txt")
                    if txt.exists():
                        txt.unlink()
            except (ValueError, OSError) as exc:
                logger.debug("Skipping invalid buffer file %s: %s", f, exc)

    def get_latest(self) -> Path | None:
        """Return path to the most recent frame, or None."""
        frames = self.list_frames()
        return frames[-1] if frames else None

    def get_latest_context(self) -> dict:
        """Return the best representation of the latest frame."""
        latest = self.get_latest()
        if latest is None:
            return {"type": "none", "content": "No frames in buffer"}

        txt_path = latest.with_suffix(".txt")
        if txt_path.exists():
            try:
                meta = json.loads(txt_path.read_text(encoding="utf-8"))
                return {"type": "text", "content": meta["text"]}
            except (json.JSONDecodeError, KeyError):
                pass

        return {"type": "image", "path": latest}

    def list_frames(self) -> list[Path]:
        """Return all buffered frames sorted oldest-first."""
        return sorted(BUFFER_DIR.glob("*.jpg"), key=lambda f: f.stem)

    def get_recent(self, seconds: int = 60) -> list[Path]:
        """Return frames from the last N seconds."""
        cutoff = (time.time() - seconds) * 1000
        return [f for f in self.list_frames() if int(f.stem) >= cutoff]

    def get_recent_context(self, seconds: int = 60) -> list[dict]:
        """Return best representation of each recent frame."""
        results = []
        for frame in self.get_recent(seconds):
            txt_path = frame.with_suffix(".txt")
            ts = int(frame.stem) / 1000.0
            entry = {"timestamp": ts}
            if txt_path.exists():
                try:
                    meta = json.loads(txt_path.read_text(encoding="utf-8"))
                    entry["type"] = "text"
                    entry["content"] = meta["text"]
                except (json.JSONDecodeError, KeyError):
                    entry["type"] = "image"
                    entry["path"] = str(frame)
            else:
                entry["type"] = "image"
                entry["path"] = str(frame)
            results.append(entry)
        return results

    def frame_count(self) -> int:
        return len(list(BUFFER_DIR.glob("*.jpg")))

    def clear(self):
        """Delete all buffered frames and text."""
        for f in BUFFER_DIR.glob("*"):
            f.unlink(missing_ok=True)
        self._last_frame = None
