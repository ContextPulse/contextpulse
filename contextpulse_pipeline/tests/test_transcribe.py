"""Tests for contextpulse_pipeline.transcribe — mocked Groq, idempotent skip, 429 retry."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextpulse_pipeline.manifest import AudioEntry, Manifest
from contextpulse_pipeline.transcribe import AudioTooLargeError, transcribe

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LIMIT_BYTES = 25 * 1024 * 1024


def _make_audio_file(tmp_path: Path, size_bytes: int = 1024, name: str = "clip.opus") -> Path:
    p = tmp_path / name
    p.write_bytes(b"a" * size_bytes)
    return p


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_manifest(episode: str = "ep-test") -> Manifest:
    return Manifest(episode=episode)


def _make_s3_client(existing_keys: list[str] | None = None) -> MagicMock:
    """Return a mock boto3 s3 client. existing_keys = list of S3 keys that 'exist'."""
    client = MagicMock()
    existing: set[str] = set(existing_keys or [])

    def _head_object(Bucket: str, Key: str) -> dict:  # noqa: N803
        if Key in existing:
            return {"ContentLength": 500}
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

    def _put_object(Bucket: str, Key: str, Body: object) -> dict:  # noqa: N803
        existing.add(Key)
        return {}

    client.head_object.side_effect = _head_object
    client.put_object.side_effect = _put_object
    client.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps({"text": "hello world", "segments": []}).encode())
    }
    return client


def _make_groq_response(text: str = "hello world") -> MagicMock:
    resp = MagicMock()
    resp.text = text
    segment = MagicMock()
    segment.start = 0.0
    segment.end = 2.0
    segment.text = text
    resp.segments = [segment]
    return resp


# ---------------------------------------------------------------------------
# Idempotent skip
# ---------------------------------------------------------------------------


class TestIdempotentSkip:
    def test_existing_transcript_skips_groq(self, tmp_path: Path) -> None:
        audio = _make_audio_file(tmp_path)
        sha = _sha256(audio)
        m = _make_manifest()
        entry = AudioEntry(
            sha256=sha,
            source_tier="A",
            wall_start_utc=datetime.now(timezone.utc),
            duration_sec=60.0,
            file_path=str(audio),
        )
        m.add_audio(entry, episode="ep-test")

        # Pre-populate transcript_path and mark S3 key as existing
        existing_key = f"transcripts/ep-test/{sha}.json"
        entry.transcript_path = existing_key
        s3 = _make_s3_client(existing_keys=[existing_key])

        with patch("contextpulse_pipeline.transcribe._call_groq") as mock_groq:
            result = transcribe(
                audio, episode="ep-test", manifest=m, s3_client=s3, bucket="test-bucket"
            )

        mock_groq.assert_not_called()
        assert result == existing_key

    def test_missing_transcript_calls_groq(self, tmp_path: Path) -> None:
        audio = _make_audio_file(tmp_path)
        sha = _sha256(audio)
        m = _make_manifest()
        entry = AudioEntry(
            sha256=sha,
            source_tier="A",
            wall_start_utc=datetime.now(timezone.utc),
            duration_sec=60.0,
            file_path=str(audio),
        )
        m.add_audio(entry, episode="ep-test")
        s3 = _make_s3_client()  # no existing keys

        fake_result = {"text": "hello", "segments": []}

        with patch(
            "contextpulse_pipeline.transcribe._call_groq", return_value=fake_result
        ) as mock_groq:
            result = transcribe(
                audio, episode="ep-test", manifest=m, s3_client=s3, bucket="test-bucket"
            )

        mock_groq.assert_called_once()
        assert "transcripts" in result


# ---------------------------------------------------------------------------
# 429 retry-with-wait
# ---------------------------------------------------------------------------


class TestRetryWithWait:
    def test_429_retries_up_to_3_times(self, tmp_path: Path) -> None:
        audio = _make_audio_file(tmp_path)
        sha = _sha256(audio)
        m = _make_manifest()
        entry = AudioEntry(
            sha256=sha,
            source_tier="A",
            wall_start_utc=datetime.now(timezone.utc),
            duration_sec=10.0,
            file_path=str(audio),
        )
        m.add_audio(entry, episode="ep-test")
        s3 = _make_s3_client()

        from contextpulse_pipeline.transcribe import GroqRateLimitError

        call_count = 0

        def _fail_twice_then_succeed(audio_path: Path, **kwargs: object) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise GroqRateLimitError("try again in 0m 1s")
            return {"text": "recovered", "segments": []}

        with (
            patch(
                "contextpulse_pipeline.transcribe._call_groq", side_effect=_fail_twice_then_succeed
            ),
            patch("contextpulse_pipeline.transcribe.time.sleep"),  # skip real sleep
        ):
            result = transcribe(
                audio, episode="ep-test", manifest=m, s3_client=s3, bucket="test-bucket"
            )

        assert call_count == 3
        assert result is not None

    def test_429_exhausted_raises(self, tmp_path: Path) -> None:
        """After 3 attempts all 429, should raise GroqRateLimitError."""
        audio = _make_audio_file(tmp_path)
        sha = _sha256(audio)
        m = _make_manifest()
        entry = AudioEntry(
            sha256=sha,
            source_tier="A",
            wall_start_utc=datetime.now(timezone.utc),
            duration_sec=10.0,
            file_path=str(audio),
        )
        m.add_audio(entry, episode="ep-test")
        s3 = _make_s3_client()

        from contextpulse_pipeline.transcribe import GroqRateLimitError

        def _always_rate_limit(audio_path: Path, **kwargs: object) -> dict:
            raise GroqRateLimitError("try again in 0m 1s")

        with (
            patch("contextpulse_pipeline.transcribe._call_groq", side_effect=_always_rate_limit),
            patch("contextpulse_pipeline.transcribe.time.sleep"),
        ):
            with pytest.raises(GroqRateLimitError):
                transcribe(audio, episode="ep-test", manifest=m, s3_client=s3, bucket="test-bucket")

    def test_retry_wait_parsed_from_hint(self, tmp_path: Path) -> None:
        """Sleep duration is parsed from Groq hint, not hardcoded."""
        audio = _make_audio_file(tmp_path)
        sha = _sha256(audio)
        m = _make_manifest()
        entry = AudioEntry(
            sha256=sha,
            source_tier="A",
            wall_start_utc=datetime.now(timezone.utc),
            duration_sec=10.0,
            file_path=str(audio),
        )
        m.add_audio(entry, episode="ep-test")
        s3 = _make_s3_client()

        from contextpulse_pipeline.transcribe import GroqRateLimitError

        call_count = 0
        sleep_calls: list[float] = []

        def _fail_once(audio_path: Path, **kwargs: object) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise GroqRateLimitError("try again in 1m 30s")
            return {"text": "ok", "segments": []}

        def _capture_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with (
            patch("contextpulse_pipeline.transcribe._call_groq", side_effect=_fail_once),
            patch("contextpulse_pipeline.transcribe.time.sleep", side_effect=_capture_sleep),
        ):
            transcribe(audio, episode="ep-test", manifest=m, s3_client=s3, bucket="test-bucket")

        # Should sleep ~90 + 5 = 95 seconds
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == pytest.approx(95.0)


# ---------------------------------------------------------------------------
# SDK exception translation (would have caught the v0.1 acceptance-test bug)
# ---------------------------------------------------------------------------


class TestSdkExceptionTranslation:
    """The SDK's groq.RateLimitError must be translated to the package's
    GroqRateLimitError so the retry-with-wait loop catches it.

    Regression: v0.1 shipped with _call_groq letting groq.RateLimitError escape,
    bypassing the retry loop entirely. Caught by the Phase 3 acceptance test
    on 2026-04-26 when Groq's free-tier ASPH (7200 sec/hour) was hit on file 5/7.
    """

    def test_sdk_rate_limit_translates_to_package_exception(self, tmp_path: Path) -> None:
        from contextpulse_pipeline.transcribe import GroqRateLimitError, _call_groq

        audio = _make_audio_file(tmp_path)

        # Build a fake groq SDK module with RateLimitError that mimics the real one
        fake_sdk_exc = type("RateLimitError", (Exception,), {})
        fake_client = MagicMock()
        fake_client.audio.transcriptions.create.side_effect = fake_sdk_exc(
            "Rate limit reached for whisper-large-v3 ... try again in 14m8s"
        )

        with (
            patch("groq.Groq", return_value=fake_client),
            patch("groq.RateLimitError", fake_sdk_exc),
        ):
            with pytest.raises(GroqRateLimitError) as exc_info:
                _call_groq(audio)

        assert "14m8s" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Auto-compress for large files (Rule #7)
# ---------------------------------------------------------------------------


class TestAutoCompress:
    def test_large_file_triggers_compression(self, tmp_path: Path) -> None:
        """Files >25 MB should be auto-compressed before Groq call."""
        audio = _make_audio_file(tmp_path, size_bytes=LIMIT_BYTES + 1, name="large.wav")
        compressed = tmp_path / "large.opus"
        compressed.write_bytes(b"x" * 1000)

        sha = _sha256(audio)
        m = _make_manifest()
        entry = AudioEntry(
            sha256=sha,
            source_tier="A",
            wall_start_utc=datetime.now(timezone.utc),
            duration_sec=600.0,
            file_path=str(audio),
        )
        m.add_audio(entry, episode="ep-test")
        s3 = _make_s3_client()

        with (
            patch(
                "contextpulse_pipeline.transcribe.compress_for_whisper",
                return_value=compressed,
            ) as mock_compress,
            patch(
                "contextpulse_pipeline.transcribe._call_groq",
                return_value={"text": "ok", "segments": []},
            ),
        ):
            transcribe(audio, episode="ep-test", manifest=m, s3_client=s3, bucket="test-bucket")

        mock_compress.assert_called_once()

    def test_small_file_skips_compression(self, tmp_path: Path) -> None:
        """Files under 25 MB should skip compression."""
        audio = _make_audio_file(tmp_path, size_bytes=1024, name="small.opus")
        sha = _sha256(audio)
        m = _make_manifest()
        entry = AudioEntry(
            sha256=sha,
            source_tier="A",
            wall_start_utc=datetime.now(timezone.utc),
            duration_sec=60.0,
            file_path=str(audio),
        )
        m.add_audio(entry, episode="ep-test")
        s3 = _make_s3_client()

        with (
            patch("contextpulse_pipeline.transcribe.compress_for_whisper") as mock_compress,
            patch(
                "contextpulse_pipeline.transcribe._call_groq",
                return_value={"text": "ok", "segments": []},
            ),
        ):
            transcribe(audio, episode="ep-test", manifest=m, s3_client=s3, bucket="test-bucket")

        mock_compress.assert_not_called()

    def test_cannot_compress_below_limit_raises(self, tmp_path: Path) -> None:
        """If compress still exceeds 25 MB, AudioTooLargeError is raised."""
        audio = _make_audio_file(tmp_path, size_bytes=LIMIT_BYTES + 1, name="huge.wav")
        sha = _sha256(audio)
        m = _make_manifest()
        entry = AudioEntry(
            sha256=sha,
            source_tier="A",
            wall_start_utc=datetime.now(timezone.utc),
            duration_sec=3600.0,
            file_path=str(audio),
        )
        m.add_audio(entry, episode="ep-test")
        s3 = _make_s3_client()

        from contextpulse_pipeline.compress import AudioTooLargeError as CompressError

        with patch(
            "contextpulse_pipeline.transcribe.compress_for_whisper",
            side_effect=CompressError("still too large"),
        ):
            with pytest.raises(AudioTooLargeError):
                transcribe(audio, episode="ep-test", manifest=m, s3_client=s3, bucket="test-bucket")
