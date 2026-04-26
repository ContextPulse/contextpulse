"""Integration tests for BatchPipeline with mocked S3, Groq, and Anthropic."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contextpulse_pipeline import BatchPipeline, AudioSourceTier, ContainerState
from contextpulse_pipeline.workers.batch import ConcurrentSynthesisError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_s3(existing_keys: list[str] | None = None) -> MagicMock:
    s3 = MagicMock()
    existing: set[str] = set(existing_keys or [])

    def _head(Bucket: str, Key: str) -> dict:  # noqa: N803
        if Key in existing:
            return {"ContentLength": 100}
        from botocore.exceptions import ClientError
        raise ClientError({"Error": {"Code": "404", "Message": ""}}, "HeadObject")

    def _put(Bucket: str, Key: str, Body: object) -> dict:  # noqa: N803
        existing.add(Key)
        return {}

    s3.head_object.side_effect = _head
    s3.put_object.side_effect = _put
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: b'{"text":"hello","segments":[]}')}
    return s3


def _make_audio_files(tmp_path: Path, count: int = 2) -> list[Path]:
    files = []
    for i in range(count):
        p = tmp_path / f"audio_{i}.opus"
        p.write_bytes(b"audio" * 200 + bytes([i]))  # distinct content
        files.append(p)
    return files


# ---------------------------------------------------------------------------
# BatchPipeline construction
# ---------------------------------------------------------------------------


class TestBatchPipelineConstruction:
    def test_instantiation(self, tmp_path: Path) -> None:
        s3 = _make_s3()
        pipeline = BatchPipeline(
            container="ep-test",
            config={"container_term": "episode"},
            s3_client=s3,
            bucket="test-bucket",
        )
        assert pipeline is not None

    def test_default_bucket_accepted(self) -> None:
        pipeline = BatchPipeline(
            container="ep-test",
            config={},
        )
        assert pipeline is not None


# ---------------------------------------------------------------------------
# BatchPipeline.ingest
# ---------------------------------------------------------------------------


class TestBatchPipelineIngest:
    def test_ingest_returns_manifest(self, tmp_path: Path) -> None:
        s3 = _make_s3()
        pipeline = BatchPipeline(
            container="ep-test",
            config={},
            s3_client=s3,
            bucket="test-bucket",
        )
        audio_files = _make_audio_files(tmp_path, count=2)

        with (
            patch("contextpulse_pipeline.workers.batch.transcribe") as mock_transcribe,
        ):
            mock_transcribe.side_effect = lambda audio, episode, manifest, s3_client, bucket: f"transcripts/{_sha256(audio)}.json"
            manifest = pipeline.ingest(audio_files)

        assert manifest is not None
        assert len(manifest.audio_entries) == 2

    def test_ingest_uploads_to_s3(self, tmp_path: Path) -> None:
        s3 = _make_s3()
        pipeline = BatchPipeline(
            container="ep-test",
            config={},
            s3_client=s3,
            bucket="test-bucket",
        )
        audio_files = _make_audio_files(tmp_path, count=1)

        with patch("contextpulse_pipeline.workers.batch.transcribe") as mock_transcribe:
            mock_transcribe.return_value = "transcripts/test.json"
            pipeline.ingest(audio_files)

        # S3 put_object should have been called (audio upload)
        assert s3.put_object.called

    def test_ingest_adds_entries_to_manifest(self, tmp_path: Path) -> None:
        s3 = _make_s3()
        pipeline = BatchPipeline(
            container="ep-test",
            config={},
            s3_client=s3,
            bucket="test-bucket",
        )
        audio_files = _make_audio_files(tmp_path, count=3)

        with patch("contextpulse_pipeline.workers.batch.transcribe") as mock_transcribe:
            mock_transcribe.side_effect = lambda audio, episode, manifest, s3_client, bucket: f"transcripts/{_sha256(audio)}.json"
            manifest = pipeline.ingest(audio_files)

        assert len(manifest.audio_entries) == 3


# ---------------------------------------------------------------------------
# BatchPipeline.preview
# ---------------------------------------------------------------------------


class TestBatchPipelinePreview:
    def test_preview_returns_output_map(self, tmp_path: Path) -> None:
        s3 = _make_s3()
        pipeline = BatchPipeline(
            container="ep-test",
            config={
                "synthesis_prompts": {
                    "storyline": "Write a storyline.",
                    "summary": "Write a summary.",
                }
            },
            s3_client=s3,
            bucket="test-bucket",
        )
        audio_files = _make_audio_files(tmp_path, count=1)

        with (
            patch("contextpulse_pipeline.workers.batch.transcribe") as mock_transcribe,
            patch("contextpulse_pipeline.workers.batch.synthesize") as mock_synthesize,
        ):
            mock_transcribe.return_value = "transcripts/test.json"
            mock_synthesize.return_value = {
                "storyline": "outputs/ep-test/storyline.md",
                "summary": "outputs/ep-test/summary.md",
            }
            pipeline.ingest(audio_files)
            result = pipeline.preview()

        assert "storyline" in result
        assert "summary" in result

    def test_preview_sets_partial_true(self, tmp_path: Path) -> None:
        s3 = _make_s3()
        pipeline = BatchPipeline(
            container="ep-test",
            config={"synthesis_prompts": {"summary": "Summarize."}},
            s3_client=s3,
            bucket="test-bucket",
        )
        audio_files = _make_audio_files(tmp_path, count=1)
        synthesize_calls: list[dict] = []

        def _capture_synth(**kwargs: object) -> dict:
            synthesize_calls.append(dict(kwargs))
            return {"summary": "outputs/ep-test/summary.md"}

        with (
            patch("contextpulse_pipeline.workers.batch.transcribe", return_value="t.json"),
            patch("contextpulse_pipeline.workers.batch.synthesize", side_effect=lambda *a, **kw: _capture_synth(**kw)),
        ):
            pipeline.ingest(audio_files)
            pipeline.preview()

        # At least one synthesize call with partial=True
        assert any(kw.get("partial") is True for kw in synthesize_calls)


# ---------------------------------------------------------------------------
# BatchPipeline.finalize
# ---------------------------------------------------------------------------


class TestBatchPipelineFinalize:
    def test_finalize_sets_state_finalized(self, tmp_path: Path) -> None:
        s3 = _make_s3()
        pipeline = BatchPipeline(
            container="ep-test",
            config={"synthesis_prompts": {"summary": "Summarize."}},
            s3_client=s3,
            bucket="test-bucket",
        )
        audio_files = _make_audio_files(tmp_path, count=1)

        with (
            patch("contextpulse_pipeline.workers.batch.transcribe", return_value="t.json"),
            patch("contextpulse_pipeline.workers.batch.synthesize", return_value={"summary": "s.md"}),
        ):
            pipeline.ingest(audio_files)
            pipeline.finalize()

        assert pipeline.manifest.state == ContainerState.finalized

    def test_finalize_partial_false(self, tmp_path: Path) -> None:
        s3 = _make_s3()
        pipeline = BatchPipeline(
            container="ep-test",
            config={"synthesis_prompts": {"summary": "Summarize."}},
            s3_client=s3,
            bucket="test-bucket",
        )
        audio_files = _make_audio_files(tmp_path, count=1)
        synthesize_calls: list[dict] = []

        def _capture_synth(*args: object, **kwargs: object) -> dict:
            synthesize_calls.append(kwargs)
            return {"summary": "outputs/ep-test/summary.md"}

        with (
            patch("contextpulse_pipeline.workers.batch.transcribe", return_value="t.json"),
            patch("contextpulse_pipeline.workers.batch.synthesize", side_effect=_capture_synth),
        ):
            pipeline.ingest(audio_files)
            pipeline.finalize()

        assert any(not kw.get("partial", True) for kw in synthesize_calls)


# ---------------------------------------------------------------------------
# Rule #2 — ConcurrentSynthesisError
# ---------------------------------------------------------------------------


class TestConcurrentSynthesisError:
    def test_concurrent_synthesis_raises(self, tmp_path: Path) -> None:
        """If a synthesis is already in flight, a second call should raise ConcurrentSynthesisError."""
        s3 = _make_s3()
        pipeline = BatchPipeline(
            container="ep-test",
            config={"synthesis_prompts": {"summary": "Summarize."}},
            s3_client=s3,
            bucket="test-bucket",
        )
        audio_files = _make_audio_files(tmp_path, count=1)

        with patch("contextpulse_pipeline.workers.batch.transcribe", return_value="t.json"):
            pipeline.ingest(audio_files)

        # Manually set the synthesis lock
        pipeline._synthesis_lock = True

        with pytest.raises(ConcurrentSynthesisError):
            pipeline.preview()


# ---------------------------------------------------------------------------
# AudioSourceTier (public API export)
# ---------------------------------------------------------------------------


class TestAudioSourceTier:
    def test_default_tiers_accessible(self) -> None:
        # AudioSourceTier should at least be importable and have some structure
        assert AudioSourceTier is not None

    def test_can_be_used_in_config(self) -> None:
        config = {"tier": AudioSourceTier.A}
        assert config["tier"] == AudioSourceTier.A
