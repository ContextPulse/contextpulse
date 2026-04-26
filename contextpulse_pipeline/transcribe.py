"""Groq Whisper API client — idempotent, 429-retry-with-wait.

Rule #7: auto-compress input if >25 MB.
Rule #11: parse Groq rate-limit hint, sleep + retry up to 3 times.
Rule #2: idempotent — check S3 for existing transcript before calling Groq.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from contextpulse_pipeline.compress import (
    AudioTooLargeError as CompressAudioTooLargeError,
)
from contextpulse_pipeline.compress import (
    compress_for_whisper,
)
from contextpulse_pipeline.manifest import Manifest

logger = logging.getLogger(__name__)

WHISPER_API_LIMIT_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_RETRIES = 3
RETRY_BUFFER_SECS = 5.0

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GroqRateLimitError(Exception):
    """Groq returned HTTP 429 with a retry-after hint."""


class AudioTooLargeError(Exception):
    """Audio file cannot be compressed below the 25 MB Whisper API limit."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_retry_wait(message: str) -> float:
    """Parse 'try again in Xm Ys' from Groq 429 response body.

    Returns seconds to wait (+ RETRY_BUFFER_SECS safety margin).
    Falls back to 60.0 if parsing fails.
    """
    # Match patterns like: "8m28s", "1m 30s", "0m 45s", "30s"
    pattern = r"(?:(\d+)m\s*)?(\d+)s"
    m = re.search(pattern, message, re.IGNORECASE)
    if m:
        minutes = int(m.group(1) or 0)
        seconds = int(m.group(2))
        total = minutes * 60 + seconds + RETRY_BUFFER_SECS
        logger.debug("Parsed retry-wait: %dm%ds -> sleeping %.1fs", minutes, seconds, total)
        return total
    logger.warning("Could not parse retry hint from '%s', defaulting to 65s", message[:80])
    return 60.0 + RETRY_BUFFER_SECS


def _transcript_s3_key(episode: str, sha256: str, ext: str) -> str:
    return f"transcripts/{episode}/{sha256}{ext}"


def _call_groq(audio_path: Path, **kwargs: Any) -> dict[str, Any]:
    """Call Groq Whisper API. Separated for easy mocking in tests.

    Returns dict with keys: text, segments (list of segment dicts).
    Raises GroqRateLimitError on HTTP 429 — translated from the SDK's
    groq.RateLimitError so the retry-with-wait loop in transcribe() can
    catch it (Rule #11).
    """
    try:
        from groq import Groq, RateLimitError  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError("groq package not installed. Run: uv add groq") from exc

    client = Groq()
    try:
        with audio_path.open("rb") as f:
            response = client.audio.transcriptions.create(
                file=(audio_path.name, f),
                model="whisper-large-v3",
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
    except RateLimitError as exc:
        raise GroqRateLimitError(str(exc)) from exc

    # Convert to plain dict
    segments = []
    for seg in getattr(response, "segments", []):
        segments.append(
            {
                "start": getattr(seg, "start", 0.0),
                "end": getattr(seg, "end", 0.0),
                "text": getattr(seg, "text", ""),
            }
        )
    return {"text": response.text, "segments": segments}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def transcribe(
    audio_path: Path,
    *,
    episode: str,
    manifest: Manifest,
    s3_client: Any,
    bucket: str,
) -> str:
    """Transcribe audio via Groq Whisper API, writing results to S3.

    Idempotent: checks S3 for an existing transcript (by SHA256) before
    calling the API. Returns the S3 key of the transcript JSON.

    Rules enforced:
    - Rule #7: auto-compress if >25 MB
    - Rule #11: 429 retry-with-wait (up to 3 attempts)
    - Idempotent skip if transcript already exists
    - Writes both .json (verbose with timestamps) and .txt to S3
    - Updates the matching manifest entry's transcript_path

    Args:
        audio_path: Local path to audio file.
        episode: Container episode identifier.
        manifest: The manifest for this container.
        s3_client: Boto3 S3 client.
        bucket: S3 bucket name.

    Returns:
        S3 key of the transcript JSON file.

    Raises:
        AudioTooLargeError: Cannot compress below 25 MB.
        GroqRateLimitError: Groq rate-limited after 3 retries.
    """
    sha256 = _sha256_file(audio_path)
    json_key = _transcript_s3_key(episode, sha256, ".json")
    txt_key = _transcript_s3_key(episode, sha256, ".txt")

    # --- Idempotent skip: check S3 first ---
    try:
        s3_client.head_object(Bucket=bucket, Key=json_key)
        logger.info("Transcript already exists for %s, skipping Groq call.", audio_path.name)
        # Update manifest entry if found
        _update_manifest_entry(manifest, sha256, json_key)
        return json_key
    except Exception as exc:
        # ClientError with 404 = not found; any other exception means S3 error
        if _is_not_found(exc):
            pass  # Fall through to transcription
        else:
            raise

    # --- Auto-compress if needed (Rule #7) ---
    file_to_transcribe = audio_path
    if audio_path.stat().st_size > WHISPER_API_LIMIT_BYTES:
        logger.info(
            "%s is %.1f MB, compressing before Groq call.",
            audio_path.name,
            audio_path.stat().st_size / 1e6,
        )
        try:
            compressed = compress_for_whisper(audio_path)
            file_to_transcribe = compressed
        except CompressAudioTooLargeError as exc:
            raise AudioTooLargeError(str(exc)) from exc

    # --- Groq API call with 429 retry (Rule #11) ---
    result: dict[str, Any] | None = None
    last_exc: GroqRateLimitError | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = _call_groq(file_to_transcribe)
            break
        except GroqRateLimitError as exc:
            last_exc = exc
            if attempt >= MAX_RETRIES:
                raise
            wait_secs = _parse_retry_wait(str(exc))
            logger.warning(
                "Groq 429 on attempt %d/%d. Sleeping %.1fs: %s",
                attempt,
                MAX_RETRIES,
                wait_secs,
                str(exc)[:100],
            )
            time.sleep(wait_secs)

    if result is None:
        raise last_exc or GroqRateLimitError("Groq call failed after retries")

    # --- Write to S3 (Rule #8: only on full success) ---
    json_body = json.dumps(result, ensure_ascii=False).encode("utf-8")
    txt_body = result.get("text", "").encode("utf-8")

    s3_client.put_object(Bucket=bucket, Key=json_key, Body=json_body)
    s3_client.put_object(Bucket=bucket, Key=txt_key, Body=txt_body)

    logger.info("Transcribed %s -> s3://%s/%s", audio_path.name, bucket, json_key)

    # Update manifest entry
    _update_manifest_entry(manifest, sha256, json_key)

    return json_key


def _is_not_found(exc: Exception) -> bool:
    """Return True if exception indicates S3 key does not exist (404)."""
    error_str = str(exc)
    return "404" in error_str or "NoSuchKey" in error_str or "Not Found" in error_str


def _update_manifest_entry(manifest: Manifest, sha256: str, transcript_path: str) -> None:
    """Find the manifest entry by SHA256 and set its transcript_path."""
    for entry in manifest.audio_entries:
        if entry.sha256 == sha256:
            entry.transcript_path = transcript_path
            return
    logger.debug(
        "No manifest entry found for sha256=%s — transcript path not stored in manifest.",
        sha256[:12],
    )
