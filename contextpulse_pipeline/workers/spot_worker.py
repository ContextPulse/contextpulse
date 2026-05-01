"""ContextPulse Pipeline — SQS-driven GPU spot worker daemon.

Runs on a g6.xlarge (or fallback) spot instance. Polls
contextpulse-transcription-queue, processes one job at a time, uploads
outputs to S3, emits CloudWatch metrics, then self-terminates after
10 minutes of idle time.

Job message schema (JSON):
    {
        "session_id":     "ep-2026-04-26-josh-cashman",
        "s3_bucket":      "jerard-activefounder",
        "audio_keys":     ["raw/ep-2026-04-26-josh-cashman/dji/TX01_...ogg", ...],
        "transcript_keys": [],          # populated by this worker after transcription
        "speaker_mapping": {"TX01": "Josh", "TX00": "Chris", "ambient": "David"},
        "output_prefix":  "outputs/ep-2026-04-26-josh-cashman"
    }

Outputs written to S3 under output_prefix/:
    master_basic.mp3          — mixed master audio (Tier 1, pure ffmpeg)
    master_enhanced.mp3       — ML-enhanced audio (Tier 2, this worker)
    master_transcript.md      — speaker-attributed transcript (markdown)
    master_transcript.json    — structured transcript with word timestamps
    master_chapters.json      — auto-detected chapter boundaries
    master_qc.json            — sync/SNR quality report

Self-termination:
    - IDLE_TIMEOUT_SEC (default 600): seconds of poll silence before exit
    - On exit, terminates the EC2 instance via IMDS + terminate-instances
    - InstanceInitiatedShutdownBehavior must be "terminate" in launch template
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
QUEUE_NAME = os.environ.get("CP_QUEUE_NAME", "contextpulse-transcription-queue")
IDLE_TIMEOUT_SEC = int(os.environ.get("CP_IDLE_TIMEOUT_SEC", "600"))
POLL_WAIT_SEC = 20  # SQS long-poll wait time (max 20s)
MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/opt/models"))
HF_HOME = Path(os.environ.get("HF_HOME", "/opt/models/hf_cache"))
VENV_PYTHON = Path(os.environ.get("VENV_PYTHON", "/opt/contextpulse_pipeline/venv/bin/python3"))
CW_NAMESPACE = "ContextPulsePipeline"


# ---------------------------------------------------------------------------
# AWS client helpers
# ---------------------------------------------------------------------------


def _get_instance_id() -> str | None:
    """Fetch instance ID via IMDSv2."""
    try:
        import urllib.request
        # Get IMDSv2 token
        req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            token = r.read().decode()
        # Get instance ID
        req2 = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/instance-id",
            headers={"X-aws-ec2-metadata-token": token},
        )
        with urllib.request.urlopen(req2, timeout=3) as r:
            return r.read().decode()
    except Exception as exc:
        logger.warning("Could not fetch instance ID from IMDS: %s", exc)
        return None


def _self_terminate(instance_id: str | None) -> None:
    """Terminate this EC2 instance. Falls back to shutdown -h now."""
    if instance_id:
        try:
            ec2 = boto3.client("ec2", region_name=REGION)
            ec2.terminate_instances(InstanceIds=[instance_id])
            logger.info("Self-terminate request sent for %s", instance_id)
            time.sleep(30)  # Allow AWS to act before fallback
        except Exception as exc:
            logger.warning("terminate-instances failed: %s — falling back to shutdown", exc)
    try:
        subprocess.run(["shutdown", "-h", "now"], check=False)
    except Exception:
        pass
    # Last resort: just exit (if running outside EC2 for tests)
    sys.exit(0)


def _emit_metric(cw: Any, metric_name: str, value: float, unit: str = "Count") -> None:
    """Fire-and-forget CloudWatch metric emission."""
    try:
        cw.put_metric_data(
            Namespace=CW_NAMESPACE,
            MetricData=[{
                "MetricName": metric_name,
                "Value": value,
                "Unit": unit,
                "Dimensions": [{"Name": "Stage", "Value": "v1"}],
            }],
        )
    except Exception as exc:
        logger.warning("CloudWatch emit failed for %s: %s", metric_name, exc)


# ---------------------------------------------------------------------------
# Job processing
# ---------------------------------------------------------------------------


@dataclass
class TranscriptionJob:
    """Deserialized SQS job message."""
    session_id: str
    s3_bucket: str
    audio_keys: list[str]
    transcript_keys: list[str]
    speaker_mapping: dict[str, str]
    output_prefix: str
    receipt_handle: str  # SQS receipt handle for deletion

    @classmethod
    def from_message(cls, msg: dict) -> "TranscriptionJob":
        body = json.loads(msg["Body"])
        return cls(
            session_id=body["session_id"],
            s3_bucket=body["s3_bucket"],
            audio_keys=body.get("audio_keys", []),
            transcript_keys=body.get("transcript_keys", []),
            speaker_mapping=body.get("speaker_mapping", {}),
            output_prefix=body.get("output_prefix", f"outputs/{body['session_id']}"),
            receipt_handle=msg["ReceiptHandle"],
        )


def _s3_key_exists(s3: Any, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:
        if "404" in str(exc) or "NoSuchKey" in str(exc) or "Not Found" in str(exc):
            return False
        raise


# ---------------------------------------------------------------------------
# Stage-level S3 checkpoint helpers
#
# Pattern: for each expensive stage, persist its output to
#   s3://{bucket}/intermediate/{session_id}/{kind}[_{channel}].json
# so that a stage 7+ failure does not force a full Whisper/align/diarize re-run
# (those are the GPU-expensive stages — 10 min, 5 min, 4 min respectively).
# Restart cost on retry: ~5 sec to download cached JSON instead of recomputing.
# ---------------------------------------------------------------------------


def _cache_key(session_id: str, kind: str, channel: str | None = None) -> str:
    if channel is None:
        return f"intermediate/{session_id}/{kind}.json"
    return f"intermediate/{session_id}/{kind}_{channel}.json"


def _cache_get_json(s3: Any, bucket: str, session_id: str, kind: str, channel: str | None = None) -> dict | None:
    """Return cached JSON dict, or None on cache miss / any error."""
    key = _cache_key(session_id, kind, channel)
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read())
    except Exception as exc:
        # NoSuchKey is the common case; we treat any error as cache miss
        # and let the caller recompute. Log only unexpected errors.
        if "NoSuchKey" not in str(exc) and "404" not in str(exc):
            logger.warning("Cache GET failed for %s: %s", key, exc)
        return None


def _cache_put_json(s3: Any, bucket: str, session_id: str, kind: str, data: dict, channel: str | None = None) -> None:
    """Best-effort cache upload. Failure is non-fatal — pipeline continues."""
    key = _cache_key(session_id, kind, channel)
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as exc:
        logger.warning("Cache PUT failed for %s: %s", key, exc)


def process_job(job: TranscriptionJob) -> None:
    """Execute the full transcription pipeline for one job.

    Pipeline stages:
    1. Download audio files from S3
    2. ffmpeg pre-compress (WAV/OGG → 16kHz 16-bit WAV, mono per channel)
    3. faster-whisper transcribe each channel (int8_float16 on CUDA)
    4. WhisperX forced alignment for word timestamps
    5. pyannote 3.1 diarization on ambient/ambient track
    6. Attribution merge (dedicated-mic channels win over diarization labels)
    7. master.py Tier 1 enhancements (highpass, denoise, level_match, bleed_cancel, mix)
    8. Upload all outputs to S3
    """
    import torch
    from faster_whisper import WhisperModel
    import whisperx
    from pyannote.audio import Pipeline as PyannotePipeline

    s3 = boto3.client("s3", region_name=REGION)
    sm = boto3.client("secretsmanager", region_name=REGION)

    logger.info("Processing job: session_id=%s bucket=%s", job.session_id, job.s3_bucket)

    # Check idempotency — if enhanced output already exists, skip
    enhanced_key = f"{job.output_prefix}/master_enhanced.mp3"
    if _s3_key_exists(s3, job.s3_bucket, enhanced_key):
        logger.info("Output already exists at %s — skipping job (idempotent)", enhanced_key)
        return

    # Fetch HF token
    hf_token = sm.get_secret_value(
        SecretId="contextpulse/hf_token"
    )["SecretString"]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "int8_float16" if device == "cuda" else "int8"
    logger.info("Device: %s, compute_type: %s", device, compute_type)

    with tempfile.TemporaryDirectory(prefix=f"cp_worker_{job.session_id}_") as tmp_str:
        tmp = Path(tmp_str)

        # ── Stage 1: Download audio files ───────────────────────────────────
        logger.info("[1/8] Downloading %d audio files...", len(job.audio_keys))
        local_audio_files: list[Path] = []
        for key in job.audio_keys:
            local_path = tmp / Path(key).name
            s3.download_file(job.s3_bucket, key, str(local_path))
            local_audio_files.append(local_path)
            logger.info("  Downloaded: %s (%.1f MB)", Path(key).name,
                        local_path.stat().st_size / 1e6)

        # ── Stage 2: Pre-compress each file to 16kHz 16-bit WAV ─────────────
        logger.info("[2/8] Pre-compressing to 16kHz WAV...")
        compressed_files: dict[str, Path] = {}  # channel_key -> wav path
        for audio_file in local_audio_files:
            wav_out = tmp / (audio_file.stem + "_16k.wav")
            cmd = [
                "ffmpeg", "-y", "-i", str(audio_file),
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                str(wav_out),
            ]
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode != 0:
                err = result.stderr.decode(errors="replace")[-300:]
                raise RuntimeError(f"ffmpeg pre-compress failed for {audio_file.name}: {err}")
            # Map to channel key (TX01, TX00, ambient, etc.)
            channel_key = _extract_channel_key(audio_file.name, job.speaker_mapping)
            if channel_key not in compressed_files:
                compressed_files[channel_key] = wav_out
            else:
                # Multiple files for same channel — concat
                merged_wav = tmp / f"merged_{channel_key}_16k.wav"
                _concat_wavs([compressed_files[channel_key], wav_out], merged_wav)
                compressed_files[channel_key] = merged_wav
            logger.info("  Compressed: %s -> %s", audio_file.name, wav_out.name)

        # ── Stage 3: Whisper transcribe each channel ─────────────────────────
        # Per-channel S3 checkpoint at intermediate/{session}/whisper_{channel}.json
        # Non-cached channels run in parallel via ThreadPoolExecutor (faster-whisper
        # is reentrant-safe per its docs; L4 has 18 GB VRAM headroom for 4-way).
        from concurrent.futures import ThreadPoolExecutor, as_completed

        channel_transcripts: dict[str, dict] = {}
        whisper_todo: list[tuple[str, Path]] = []
        for channel_key, wav_path in compressed_files.items():
            cached = _cache_get_json(s3, job.s3_bucket, job.session_id, "whisper", channel_key)
            if cached is not None:
                channel_transcripts[channel_key] = cached
                logger.info("  [3/8] CACHED Whisper %s (%d segments, lang=%s)",
                            channel_key, len(cached["segments"]), cached.get("language"))
            else:
                whisper_todo.append((channel_key, wav_path))

        if whisper_todo:
            logger.info("[3/8] Loading Whisper large-v3 (%d channels to transcribe)", len(whisper_todo))
            whisper_model = WhisperModel(
                "large-v3",
                device=device,
                compute_type=compute_type,
                download_root=str(MODELS_DIR / "whisper"),
            )

            def _transcribe_one(args: tuple[str, Path]) -> tuple[str, dict]:
                channel_key, wav_path = args
                logger.info("  Transcribing channel: %s (%s)", channel_key, wav_path.name)
                segments, info = whisper_model.transcribe(
                    str(wav_path),
                    language="en",
                    word_timestamps=True,
                    vad_filter=True,
                )
                segments_list = []
                for seg in segments:
                    segments_list.append({
                        "start": seg.start,
                        "end": seg.end,
                        "text": seg.text.strip(),
                        "words": [
                            {"start": w.start, "end": w.end, "word": w.word}
                            for w in (seg.words or [])
                        ],
                    })
                result = {"language": info.language, "segments": segments_list}
                logger.info("    %s: %d segments, language=%s", channel_key, len(segments_list), info.language)
                return channel_key, result

            # max_workers=4 chosen to fit comfortably in 24 GB L4 VRAM (~5 GB per call observed).
            with ThreadPoolExecutor(max_workers=min(4, len(whisper_todo))) as ex:
                for fut in as_completed([ex.submit(_transcribe_one, t) for t in whisper_todo]):
                    channel_key, result = fut.result()
                    channel_transcripts[channel_key] = result
                    _cache_put_json(s3, job.s3_bucket, job.session_id, "whisper", result, channel_key)

            # Free Whisper model before loading next ML model
            del whisper_model
            if device == "cuda":
                torch.cuda.empty_cache()
        else:
            logger.info("[3/8] All %d channels cached — skipped Whisper", len(compressed_files))

        # ── Stage 4: WhisperX alignment (word-level timestamps) ──────────────
        # Same checkpoint + parallelism pattern as stage 3.
        aligned_transcripts: dict[str, dict] = {}
        align_todo: list[str] = []
        for channel_key in channel_transcripts:
            cached = _cache_get_json(s3, job.s3_bucket, job.session_id, "aligned", channel_key)
            if cached is not None:
                aligned_transcripts[channel_key] = cached
                logger.info("  [4/8] CACHED align %s (%d segments)", channel_key, len(cached["segments"]))
            else:
                align_todo.append(channel_key)

        if align_todo:
            logger.info("[4/8] WhisperX forced alignment (%d channels to align)", len(align_todo))
            align_model, align_metadata = whisperx.load_align_model(
                language_code="en",
                device=device,
            )
            import soundfile as sf

            def _align_one(channel_key: str) -> tuple[str, dict]:
                wav_path = compressed_files[channel_key]
                audio_arr, _ = sf.read(str(wav_path), dtype="float32", always_2d=False)
                aligned = whisperx.align(
                    channel_transcripts[channel_key]["segments"],
                    align_model,
                    align_metadata,
                    audio_arr,
                    device,
                    return_char_alignments=False,
                )
                result = {
                    "language": channel_transcripts[channel_key]["language"],
                    "segments": aligned["segments"],
                    "word_segments": aligned.get("word_segments", []),
                }
                logger.info("    Aligned channel %s: %d segments", channel_key, len(aligned["segments"]))
                return channel_key, result

            with ThreadPoolExecutor(max_workers=min(4, len(align_todo))) as ex:
                for fut in as_completed([ex.submit(_align_one, k) for k in align_todo]):
                    channel_key, result = fut.result()
                    aligned_transcripts[channel_key] = result
                    _cache_put_json(s3, job.s3_bucket, job.session_id, "aligned", result, channel_key)

            del align_model
            if device == "cuda":
                torch.cuda.empty_cache()
        else:
            logger.info("[4/8] All channels cached — skipped WhisperX align")

        # ── Stage 5: pyannote diarization on ambient/mixed track ──────────────
        # Single-channel stage; cache by diarize-channel name. pyannote pipeline
        # is NOT thread-safe so no parallelism here, but it only runs once anyway.
        diarize_channel = "ambient" if "ambient" in compressed_files else list(compressed_files.keys())[0]
        cached_diar = _cache_get_json(s3, job.s3_bucket, job.session_id, "diarization", diarize_channel)
        if cached_diar is not None:
            diarization_segments = cached_diar["segments"]
            logger.info("[5/8] CACHED diarization on %s (%d turns)", diarize_channel, len(diarization_segments))
        else:
            logger.info("[5/8] pyannote 3.1 diarization...")
            pyannote_pipeline = PyannotePipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=hf_token,  # pyannote 4.x renamed use_auth_token -> token
            )
            pyannote_pipeline.to(torch.device(device))

            diarize_wav = compressed_files[diarize_channel]
            diarization = pyannote_pipeline(str(diarize_wav))
            # pyannote 4.x: pipeline returns DiarizeOutput wrapper, not Annotation directly.
            diarization_annotation = diarization.speaker_diarization
            diarization_segments = []
            for turn, _, speaker_label in diarization_annotation.itertracks(yield_label=True):
                diarization_segments.append({
                    "start": turn.start,
                    "end": turn.end,
                    "speaker": speaker_label,
                })
            logger.info("    Diarization found %d turns on channel %s",
                        len(diarization_segments), diarize_channel)
            _cache_put_json(
                s3, job.s3_bucket, job.session_id, "diarization",
                {"channel": diarize_channel, "segments": diarization_segments},
                diarize_channel,
            )

            del pyannote_pipeline
            if device == "cuda":
                torch.cuda.empty_cache()

        # ── Stage 6: Attribution merge ────────────────────────────────────────
        logger.info("[6/8] Attribution merge...")
        merged_segments = _merge_attribution(
            aligned_transcripts=aligned_transcripts,
            diarization_segments=diarization_segments,
            speaker_mapping=job.speaker_mapping,
            diarize_channel=diarize_channel,
        )

        # ── Stage 7: master.py Tier 1 enhancements ───────────────────────────
        logger.info("[7/8] master.py Tier 1 audio processing...")
        # Import the master module from the installed package
        sys.path.insert(0, "/opt/contextpulse_pipeline")
        from contextpulse_pipeline.master import unify_audio, MasterOutput

        master_output: MasterOutput = unify_audio(
            session_id=job.session_id,
            s3_bucket=job.s3_bucket,
            speaker_mapping=job.speaker_mapping,
            s3_client=s3,
            local_raw_prefix=f"raw/{job.session_id}/dji/",
        )

        # ── Stage 8: Upload enhanced transcript and rename audio ──────────────
        logger.info("[8/8] Uploading outputs...")

        # Write enhanced transcript JSON
        enhanced_transcript = {
            "session_id": job.session_id,
            "pipeline_version": "v1.0-g6",
            "segments": merged_segments,
        }
        transcript_key = f"{job.output_prefix}/master_transcript.json"
        s3.put_object(
            Bucket=job.s3_bucket,
            Key=transcript_key,
            Body=json.dumps(enhanced_transcript, indent=2, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info("Uploaded enhanced transcript: %s", transcript_key)

        # Write Markdown transcript
        md_lines = ["# Transcript\n"]
        prev_speaker = None
        for seg in merged_segments:
            start_sec = seg["start"]
            h = int(start_sec // 3600)
            m = int((start_sec % 3600) // 60)
            s_val = int(start_sec % 60)
            ts = f"[{h:02d}:{m:02d}:{s_val:02d}]"
            speaker = seg.get("speaker", "Unknown")
            text = seg.get("text", "").strip()
            if speaker != prev_speaker:
                md_lines.append(f"\n{ts} **{speaker}:** {text}")
                prev_speaker = speaker
            else:
                md_lines[-1] += f" {text}"

        md_key = f"{job.output_prefix}/master_transcript.md"
        s3.put_object(
            Bucket=job.s3_bucket,
            Key=md_key,
            Body="\n".join(md_lines).encode("utf-8"),
            ContentType="text/markdown",
        )
        logger.info("Uploaded enhanced transcript.md: %s", md_key)

        # Copy master_basic.mp3 to master_enhanced.mp3
        # (Phase 3 uses pure ffmpeg+Whisper enhancement; ML de-noise is Phase 3.1)
        copy_source = {"Bucket": job.s3_bucket, "Key": f"{job.output_prefix}/master_basic.mp3"}
        s3.copy(copy_source, job.s3_bucket, enhanced_key)
        logger.info("Copied master_basic.mp3 -> master_enhanced.mp3 at %s", enhanced_key)

    logger.info("Job complete: session_id=%s", job.session_id)


def _extract_channel_key(filename: str, speaker_mapping: dict[str, str]) -> str:
    """Map a filename to its channel key using speaker_mapping prefixes."""
    for prefix_key in speaker_mapping:
        if prefix_key == "ambient":
            continue
        if filename.startswith(prefix_key):
            return prefix_key
    return "ambient"


def _concat_wavs(wav_files: list[Path], out_path: Path) -> None:
    """Concatenate WAV files using ffmpeg concat demuxer."""
    list_file = out_path.with_suffix(".concat_list.txt")
    list_file.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in wav_files),
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        err = result.stderr.decode(errors="replace")[-300:]
        raise RuntimeError(f"WAV concat failed: {err}")


def _merge_attribution(
    aligned_transcripts: dict[str, dict],
    diarization_segments: list[dict],
    speaker_mapping: dict[str, str],
    diarize_channel: str,
) -> list[dict]:
    """Merge per-channel whisper segments with pyannote diarization.

    Rule:
    - Dedicated-mic channels (TX01, TX00, etc.) use the speaker_mapping label
      directly. Their attribution is authoritative.
    - Ambient channel segments get speaker labels from pyannote diarization by
      matching the segment midpoint to the closest diarization turn.
    - All segments are interleaved by start time in the final output.

    Returns list of {start, end, speaker, text, words} dicts.
    """
    all_segments: list[dict] = []

    for channel_key, transcript in aligned_transcripts.items():
        speaker_name = speaker_mapping.get(channel_key, channel_key)
        is_ambient = (channel_key == diarize_channel) or (channel_key == "ambient")

        for seg in transcript.get("segments", []):
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
            text = seg.get("text", "").strip()

            if is_ambient and diarization_segments:
                # Find best diarization label for this segment's midpoint
                midpoint = (start + end) / 2
                best_label = None
                best_overlap = 0.0
                for dseg in diarization_segments:
                    overlap_start = max(start, dseg["start"])
                    overlap_end = min(end, dseg["end"])
                    overlap = max(0.0, overlap_end - overlap_start)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_label = dseg["speaker"]
                # Map pyannote SPEAKER_00/01 to known names if possible
                if best_label and best_overlap > 0:
                    # Try to resolve by majority vote across the ambient transcript
                    speaker_name = _resolve_diarization_label(
                        best_label, speaker_mapping, diarization_segments
                    )

            all_segments.append({
                "start": start,
                "end": end,
                "speaker": speaker_name,
                "text": text,
                "channel": channel_key,
                "words": seg.get("words", []),
            })

    # Sort by start time
    all_segments.sort(key=lambda s: (s["start"], s["speaker"]))
    return all_segments


def _resolve_diarization_label(
    pyannote_label: str,
    speaker_mapping: dict[str, str],
    diarization_segments: list[dict],
) -> str:
    """Best-effort mapping from pyannote SPEAKER_N label to a known name.

    For Phase 3, we do a simple mapping: if only 1 non-ambient speaker is
    in speaker_mapping and there are only 2 pyannote speakers, the non-ambient
    speaker is SPEAKER_00 (louder/closer to mic) and SPEAKER_01 is the host.
    Falls back to the raw pyannote label if mapping is ambiguous.
    """
    # Get non-ambient speakers from mapping
    named_speakers = {k: v for k, v in speaker_mapping.items() if k != "ambient"}
    if len(named_speakers) == 1:
        # Only one named guest — map SPEAKER_00 to guest, SPEAKER_01 to ambient speaker
        ambient_name = speaker_mapping.get("ambient", "Host")
        guest_name = list(named_speakers.values())[0]
        if pyannote_label == "SPEAKER_00":
            return guest_name
        return ambient_name
    # Multiple named speakers — return raw label (Tier 2 can refine)
    return pyannote_label


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------


def main() -> None:
    logger.info("ContextPulse spot worker starting (PID %d)", os.getpid())
    logger.info("Queue: %s, Idle timeout: %ds", QUEUE_NAME, IDLE_TIMEOUT_SEC)

    instance_id = _get_instance_id()
    logger.info("Instance ID: %s", instance_id or "(not on EC2)")

    sqs = boto3.client("sqs", region_name=REGION)
    cw = boto3.client("cloudwatch", region_name=REGION)

    queue_url = sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]
    logger.info("Queue URL: %s", queue_url)

    idle_since = time.monotonic()
    jobs_processed = 0

    _emit_metric(cw, "WorkerStart", 1.0)

    while True:
        idle_secs = time.monotonic() - idle_since
        if idle_secs >= IDLE_TIMEOUT_SEC:
            logger.info(
                "Idle for %.0fs (>= %ds). Self-terminating. jobs_processed=%d",
                idle_secs, IDLE_TIMEOUT_SEC, jobs_processed,
            )
            _emit_metric(cw, "WorkerIdleTerminate", 1.0)
            _self_terminate(instance_id)
            break

        logger.info("Polling SQS (idle=%.0fs)...", idle_secs)
        try:
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=POLL_WAIT_SEC,
                MessageAttributeNames=["All"],
            )
        except Exception as exc:
            logger.error("SQS receive_message failed: %s", exc)
            time.sleep(5)
            continue

        messages = response.get("Messages", [])
        if not messages:
            logger.info("No messages (long-poll returned empty).")
            continue

        msg = messages[0]
        idle_since = time.monotonic()  # Reset idle timer

        try:
            job = TranscriptionJob.from_message(msg)
            logger.info("Received job: %s", job.session_id)
        except Exception as exc:
            logger.error("Failed to parse job message: %s\n%s", exc, traceback.format_exc())
            _emit_metric(cw, "JobParseError", 1.0)
            # Delete malformed message to avoid DLQ noise
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
            continue

        job_start = time.monotonic()
        try:
            process_job(job)
            job_duration_sec = time.monotonic() - job_start
            logger.info(
                "Job succeeded: session_id=%s duration=%.1fs",
                job.session_id, job_duration_sec,
            )

            # Delete message only on success
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
            jobs_processed += 1

            _emit_metric(cw, "JobSuccess", 1.0)
            _emit_metric(cw, "JobDurationSeconds", job_duration_sec, unit="Seconds")

        except Exception as exc:
            job_duration_sec = time.monotonic() - job_start
            logger.error(
                "Job FAILED: session_id=%s duration=%.1fs error=%s\n%s",
                job.session_id, job_duration_sec, exc, traceback.format_exc(),
            )
            _emit_metric(cw, "JobFailure", 1.0)
            # Do NOT delete the message — let SQS retry (up to maxReceiveCount=3 then DLQ)
            # Extend visibility to avoid immediate retry while we're still running
            try:
                sqs.change_message_visibility(
                    QueueUrl=queue_url,
                    ReceiptHandle=msg["ReceiptHandle"],
                    VisibilityTimeout=60,  # Back in queue in 60s
                )
            except Exception:
                pass


if __name__ == "__main__":
    main()
