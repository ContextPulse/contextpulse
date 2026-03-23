"""Rolling screenshot buffer with change detection and OCR classification.

Keeps only the last N minutes of captures on disk. Skips frames that are
visually identical to the previous one (< threshold % pixel difference).

Each stored frame has a paired .txt file if OCR detected enough text.
The MCP server uses the .txt when available (cheap) and falls back to
the .jpg (expensive) only for visual content.

Frame filenames include monitor index: {timestamp_ms}_m{monitor_index}.jpg
"""

import json
import logging
import re
import time
from pathlib import Path

import numpy as np
from PIL import Image

from contextpulse_sight.config import (
    BUFFER_DIR,
    BUFFER_MAX_AGE,
    CHANGE_THRESHOLD,
    JPEG_QUALITY,
    STORAGE_MODE,
)

logger = logging.getLogger("contextpulse.sight.buffer")

# Pattern to parse frame filenames: {timestamp}_m{monitor}.jpg or .txt
_FRAME_RE = re.compile(r"^(\d+)_m(\d+)\.(jpg|txt)$")


def estimate_image_tokens(width: int, height: int) -> int:
    """Estimate Claude API token cost for sending an image.

    Uses Claude's public formula: ceil(width/768) * ceil(height/768) * 258.
    """
    import math
    tiles_w = math.ceil(width / 768)
    tiles_h = math.ceil(height / 768)
    return tiles_w * tiles_h * 258


def estimate_text_tokens(text: str) -> int:
    """Estimate Claude API token cost for sending text (~4 chars per token)."""
    return max(1, len(text) // 4)


def parse_frame_path(path: Path) -> tuple[int, int] | None:
    """Extract (timestamp_ms, monitor_index) from a buffer frame path.

    Works with both .jpg and .txt files (text-only frames).
    Returns None if the filename doesn't match the expected format.
    Also handles legacy format (just timestamp, no monitor index).
    """
    m = _FRAME_RE.match(path.name)
    if m:
        return int(m.group(1)), int(m.group(2))
    # Legacy format: {timestamp}.jpg (no monitor index)
    stem = path.stem
    try:
        return int(stem), 0
    except ValueError:
        return None


class RollingBuffer:
    def __init__(self):
        BUFFER_DIR.mkdir(parents=True, exist_ok=True)
        self._last_frames: dict[int, np.ndarray] = {}  # per-monitor
        self._prune()

    def add(self, img: Image.Image, monitor_index: int = 0) -> tuple[Path, float] | bool:
        """Add a frame if it differs from the last one for this monitor.

        Returns (frame_path, diff_pct) if stored, False if skipped.
        diff_pct is the percentage of pixel difference (0-100).
        """
        arr = np.asarray(img)

        last = self._last_frames.get(monitor_index)
        if last is not None:
            diff_pct = self._diff_pct(arr, last)
            if diff_pct < CHANGE_THRESHOLD:
                return False
        else:
            diff_pct = 100.0  # first frame for this monitor

        self._last_frames[monitor_index] = arr
        ts = int(time.time() * 1000)
        path = BUFFER_DIR / f"{ts}_m{monitor_index}.jpg"
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(path, format="JPEG", quality=JPEG_QUALITY)
        self._prune()
        return path, round(diff_pct, 1)

    def add_text_only(self, text: str, confidence: float, monitor_index: int = 0) -> Path:
        """Store a text-only frame (no image). Returns the .txt path."""
        ts = int(time.time() * 1000)
        # Use .txt as primary file, no .jpg
        txt_path = BUFFER_DIR / f"{ts}_m{monitor_index}.txt"
        meta = {
            "text": text,
            "confidence": round(confidence, 2),
            "timestamp": str(ts),
            "text_only": True,
        }
        txt_path.write_text(json.dumps(meta), encoding="utf-8")
        self._prune()
        return txt_path

    def add_ocr_text(self, frame_path: Path, text: str, confidence: float):
        """Store OCR text alongside a frame. Called async after OCR completes."""
        txt_path = frame_path.with_suffix(".txt")
        parsed = parse_frame_path(frame_path)
        meta = {
            "text": text,
            "confidence": round(confidence, 2),
            "timestamp": str(parsed[0]) if parsed else frame_path.stem,
        }
        txt_path.write_text(json.dumps(meta), encoding="utf-8")

    def _diff_pct(self, current: np.ndarray, last: np.ndarray) -> float:
        """Compute the percentage of pixel difference between two frames (0-100)."""
        if last.shape != current.shape:
            return 100.0

        diff = np.mean(np.abs(current.astype(np.int16) - last.astype(np.int16)))
        return diff / 255.0 * 100.0

    def _has_changed(self, current: np.ndarray, last: np.ndarray) -> bool:
        """Compare current frame to last. Returns True if meaningfully different."""
        return self._diff_pct(current, last) >= CHANGE_THRESHOLD

    def _prune(self):
        """Remove frames older than BUFFER_MAX_AGE seconds."""
        cutoff = (time.time() - BUFFER_MAX_AGE) * 1000
        # Prune .jpg files and their .txt sidecars
        for f in BUFFER_DIR.glob("*.jpg"):
            try:
                parsed = parse_frame_path(f)
                if parsed is None:
                    continue
                if parsed[0] < cutoff:
                    f.unlink()
                    txt = f.with_suffix(".txt")
                    if txt.exists():
                        txt.unlink()
            except OSError as exc:
                logger.debug("Skipping invalid buffer file %s: %s", f, exc)
        # Prune text-only .txt files (no matching .jpg)
        for f in BUFFER_DIR.glob("*.txt"):
            try:
                jpg = f.with_suffix(".jpg")
                if jpg.exists():
                    continue  # This is a sidecar, handled above
                parsed = parse_frame_path(f)
                if parsed is None:
                    continue
                if parsed[0] < cutoff:
                    f.unlink()
            except OSError as exc:
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
        """Return all buffered frames sorted oldest-first by timestamp.

        Includes both .jpg image frames and text-only .txt frames.
        For .txt files that are sidecars to a .jpg, only the .jpg is listed.
        """
        seen_stems = set()
        frames = []
        # Collect .jpg files
        for f in BUFFER_DIR.glob("*.jpg"):
            parsed = parse_frame_path(f)
            if parsed:
                seen_stems.add(f.stem)
                frames.append(f)
        # Collect text-only .txt files (not sidecars)
        for f in BUFFER_DIR.glob("*.txt"):
            if f.stem not in seen_stems:
                parsed = parse_frame_path(f)
                if parsed:
                    frames.append(f)

        def sort_key(f: Path):
            parsed = parse_frame_path(f)
            return parsed[0] if parsed else 0
        return sorted(frames, key=sort_key)

    def get_recent(self, seconds: int = 60) -> list[Path]:
        """Return frames from the last N seconds."""
        cutoff = (time.time() - seconds) * 1000
        results = []
        for f in self.list_frames():
            parsed = parse_frame_path(f)
            if parsed and parsed[0] >= cutoff:
                results.append(f)
        return results

    def get_recent_context(self, seconds: int = 60) -> list[dict]:
        """Return best representation of each recent frame."""
        results = []
        for frame in self.get_recent(seconds):
            txt_path = frame.with_suffix(".txt")
            parsed = parse_frame_path(frame)
            ts = parsed[0] / 1000.0 if parsed else 0
            entry = {"timestamp": ts, "monitor_index": parsed[1] if parsed else 0}
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
        """Count all frames (image + text-only)."""
        return len(self.list_frames())

    def clear(self):
        """Delete all buffered frames and text."""
        for f in BUFFER_DIR.glob("*"):
            f.unlink(missing_ok=True)
        self._last_frames.clear()
