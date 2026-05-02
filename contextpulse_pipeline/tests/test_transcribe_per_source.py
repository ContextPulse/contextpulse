"""Tests for contextpulse_pipeline.transcribe_per_source — per-source Whisper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextpulse_pipeline.raw_source import RawSource, RawSourceCollection
from contextpulse_pipeline.transcribe_per_source import (
    transcribe_collection,
    transcribe_raw_source,
)

# Real fixtures
TELEGRAM_SMALL = Path(
    "C:/Users/david/AppData/Local/Temp/josh-narrative/telegram/final-1777215323203.mp3"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw_source(
    *,
    sha256: str = "a" * 64,
    file_path: str,
    container: str = "ep-test",
    source_tier: str = "C",
    duration_sec: float = 60.0,
    sample_rate: int = 48000,
    channel_count: int = 1,
    codec: str = "mp3",
    bit_depth: int | None = None,
) -> RawSource:
    return RawSource(
        sha256=sha256,
        file_path=file_path,
        container=container,
        source_tier=source_tier,
        duration_sec=duration_sec,
        sample_rate=sample_rate,
        channel_count=channel_count,
        codec=codec,
        bit_depth=bit_depth,
    )


def _stub_transcribe_func(audio_path: Path, *, model: str) -> dict:
    """Mock backend — returns a synthetic Whisper-shaped result."""
    return {
        "language": "en",
        "duration": 12.34,
        "text": "Hello world. This is a stub.",
        "segments": [
            {
                "start": 0.0,
                "end": 5.0,
                "text": "Hello world.",
                "avg_logprob": -0.2,
                "compression_ratio": 1.5,
                "no_speech_prob": 0.001,
            },
            {
                "start": 5.0,
                "end": 10.0,
                "text": "This is a stub.",
                "avg_logprob": -0.25,
                "compression_ratio": 1.4,
                "no_speech_prob": 0.001,
            },
        ],
    }


# ---------------------------------------------------------------------------
# transcribe_raw_source — happy path with stubbed backend
# ---------------------------------------------------------------------------


class TestTranscribeRawSource:
    def test_writes_json_and_txt(self, tmp_path: Path) -> None:
        # Build a fake 1 MB MP3 to ensure no compression triggers
        audio = tmp_path / "tiny.mp3"
        audio.write_bytes(b"\x00" * (1024 * 1024))
        rs = _make_raw_source(file_path=str(audio))
        json_path = transcribe_raw_source(
            rs, tmp_path / "transcripts", transcribe_func=_stub_transcribe_func
        )
        assert json_path.exists()
        assert json_path.suffix == ".json"
        # txt sidecar
        txt_path = json_path.with_suffix(".txt")
        assert txt_path.exists()

    def test_json_schema_includes_required_fields(self, tmp_path: Path) -> None:
        audio = tmp_path / "tiny.mp3"
        audio.write_bytes(b"\x00" * (1024 * 1024))
        rs = _make_raw_source(file_path=str(audio))
        json_path = transcribe_raw_source(
            rs, tmp_path / "out", transcribe_func=_stub_transcribe_func
        )
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        assert doc["session_id"] == "ep-test"
        assert doc["source_sha256"] == "a" * 64
        assert doc["source_path"] == str(audio)
        assert doc["source_tier"] == "C"
        assert doc["model"] == "whisper-large-v3"
        assert doc["language"] == "en"
        assert "text" in doc
        assert "segments" in doc
        assert len(doc["segments"]) == 2
        # Segment shape
        seg = doc["segments"][0]
        assert "start" in seg and "end" in seg and "text" in seg

    def test_skips_when_output_exists(self, tmp_path: Path) -> None:
        audio = tmp_path / "tiny.mp3"
        audio.write_bytes(b"\x00" * (1024 * 1024))
        rs = _make_raw_source(file_path=str(audio))
        out = tmp_path / "out"

        calls = {"n": 0}

        def counting_stub(audio_path: Path, *, model: str) -> dict:
            calls["n"] += 1
            return _stub_transcribe_func(audio_path, model=model)

        # First call — produces transcript
        transcribe_raw_source(rs, out, transcribe_func=counting_stub)
        assert calls["n"] == 1

        # Second call — should skip
        transcribe_raw_source(rs, out, transcribe_func=counting_stub)
        assert calls["n"] == 1  # not incremented

    def test_skip_existing_false_forces_retranscribe(self, tmp_path: Path) -> None:
        audio = tmp_path / "tiny.mp3"
        audio.write_bytes(b"\x00" * (1024 * 1024))
        rs = _make_raw_source(file_path=str(audio))
        out = tmp_path / "out"
        calls = {"n": 0}

        def counting_stub(audio_path: Path, *, model: str) -> dict:
            calls["n"] += 1
            return _stub_transcribe_func(audio_path, model=model)

        transcribe_raw_source(rs, out, transcribe_func=counting_stub)
        transcribe_raw_source(rs, out, transcribe_func=counting_stub, skip_existing=False)
        assert calls["n"] == 2

    def test_missing_source_file_raises(self, tmp_path: Path) -> None:
        rs = _make_raw_source(file_path=str(tmp_path / "does-not-exist.mp3"))
        with pytest.raises(FileNotFoundError):
            transcribe_raw_source(rs, tmp_path / "out", transcribe_func=_stub_transcribe_func)


# ---------------------------------------------------------------------------
# transcribe_collection
# ---------------------------------------------------------------------------


class TestTranscribeCollection:
    def test_n_sources_yield_n_json_paths(self, tmp_path: Path) -> None:
        # Three small synthetic sources
        sources = []
        for i in range(3):
            audio = tmp_path / f"src{i}.mp3"
            audio.write_bytes(b"\x00" * 4096)
            sources.append(_make_raw_source(sha256=str(i) * 64, file_path=str(audio)))
        coll = RawSourceCollection(container="ep-test", sources=sources)
        paths = transcribe_collection(coll, tmp_path / "out", transcribe_func=_stub_transcribe_func)
        assert len(paths) == 3
        for p in paths:
            assert p.exists()


# ---------------------------------------------------------------------------
# Compression integration — real ffmpeg, real WAV
# ---------------------------------------------------------------------------


class TestCompressionIntegration:
    def test_large_wav_triggers_compress(self, tmp_path: Path) -> None:
        """Verify the compress-then-transcribe path executes on a > 25 MB WAV."""
        # Build a fake 26 MB file with WAV header so compress.py's ffmpeg accepts it.
        # Using a real silent WAV via ffmpeg saves us hand-building one.
        import subprocess

        big_wav = tmp_path / "big.wav"
        # 30 sec of silence at 48k stereo 24-bit ≈ 8 MB — make it 200 sec ≈ 55 MB
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "200",
            "-c:a",
            "pcm_s24le",
            str(big_wav),
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            pytest.skip("ffmpeg unavailable for fixture build")
        assert big_wav.stat().st_size > 25 * 1024 * 1024  # > 25 MB

        rs = _make_raw_source(
            sha256="b" * 64,
            file_path=str(big_wav),
            source_tier="A",
            codec="pcm_s24le",
            sample_rate=48000,
            channel_count=2,
            bit_depth=24,
        )

        # Track the path passed to the backend — should be a .opus, not the .wav
        seen: dict[str, Path] = {}

        def capturing_stub(audio_path: Path, *, model: str) -> dict:
            seen["path"] = audio_path
            return _stub_transcribe_func(audio_path, model=model)

        transcribe_raw_source(rs, tmp_path / "out", transcribe_func=capturing_stub)
        assert seen["path"].suffix == ".opus"
        # Tmp opus should be cleaned up after
        assert not seen["path"].exists()


# ---------------------------------------------------------------------------
# Real Telegram chunk — local faster-whisper, integration test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not TELEGRAM_SMALL.exists(), reason="Telegram small fixture not present")
class TestRealTelegramTranscribe:
    @pytest.mark.timeout(300)
    def test_real_telegram_chunk_via_local_faster_whisper(self, tmp_path: Path) -> None:
        """Smoke test the real backend on the smallest Telegram chunk (~14 min audio).

        Uses 'tiny' model to keep runtime under timeout. The transcribe_func
        contract is the same; large-v3 produces better quality but slower.
        """
        from contextpulse_pipeline.ingest import ingest_file

        rs = ingest_file(TELEGRAM_SMALL, container="ep-test")
        json_path = transcribe_raw_source(
            rs,
            tmp_path / "out",
            model="tiny",  # tiny for speed in CI
        )
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        assert len(doc["segments"]) > 10
        assert len(doc["text"]) > 100
        assert doc["language"] == "en"
        assert doc["model"] == "whisper-tiny"
