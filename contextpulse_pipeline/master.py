"""Tier 1 audio unification pipeline — pure ffmpeg + Python, no ML dependencies.

Orchestrates per-channel OGG files (from DJI lavalier mics) into a single
podcast-ready MP3, a merged speaker-attributed transcript, auto-detected
chapters, and a QC report.

Tier 2 (separate agent) will layer on top of the outputs produced here:
- master_basic.mp3  ->  master_enhanced.mp3  (ML noise reduction, de-reverb)
- master_transcript.md  ->  overwritten with pyannote diarization
- master_transcript.json  ->  overwritten with WhisperX word-level alignment

Interface contract for Tier 2:
- All outputs live under s3://<bucket>/outputs/<session_id>/
- master_transcript.json schema: {"segments": [{start, end, speaker, text}, ...]}
- master_qc.json schema: {sync_drift_ms, duration_match, snr_per_channel, transcript_alignment}
- MasterOutput dataclass is importable: `from contextpulse_pipeline.master import MasterOutput`
- Each channel's processed audio is kept in the local temp dir until caller
  disposes of it — Tier 2 can receive the temp dir path via extended API.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audio-stage S3 checkpoint helpers
#
# Tier 1 audio mix is CPU-heavy (concat+filter+bleed_cancel for 7 channels of
# ~1-2 hr audio takes ~12-15 min on g6.xlarge). Cache each stage's per-channel
# output to s3://{bucket}/intermediate/{session_id}/master/{kind}_{channel}.{ext}
# so retries skip the work that already succeeded. Cost: ~2 sec download per
# cached file vs ~2 min per stage per channel.
# ---------------------------------------------------------------------------


def _audio_cache_key(session_id: str, kind: str, channel: str, ext: str) -> str:
    return f"intermediate/{session_id}/master/{kind}_{channel}.{ext}"


def _audio_cache_get(s3: Any, bucket: str, session_id: str, kind: str, channel: str, dest_path: Path, ext: str) -> bool:
    """Download cached audio to dest_path. Returns True on hit, False on miss."""
    key = _audio_cache_key(session_id, kind, channel, ext)
    try:
        s3.download_file(bucket, key, str(dest_path))
        return True
    except Exception:
        return False


def _audio_cache_put(s3: Any, bucket: str, session_id: str, kind: str, channel: str, src_path: Path, ext: str) -> None:
    """Best-effort upload — failure is non-fatal."""
    key = _audio_cache_key(session_id, kind, channel, ext)
    try:
        s3.upload_file(str(src_path), bucket, key)
    except Exception as exc:
        logger.warning("Audio cache PUT failed for %s: %s", key, exc)


# ---------------------------------------------------------------------------
# Output dataclass — clean interface for Tier 2 to read
# ---------------------------------------------------------------------------


@dataclass
class MasterOutput:
    """S3 URIs and metadata produced by unify_audio().

    All URI fields are fully-qualified s3:// URIs. Tier 2 reads these and
    produces master_enhanced.mp3 plus overwritten transcript files.
    """

    audio_s3_uri: str
    transcript_md_s3_uri: str
    transcript_json_s3_uri: str
    chapters_json_s3_uri: str
    qc_json_s3_uri: str
    duration_sec: float
    speakers_detected: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Timestamp parsing — DJI filename convention
# ---------------------------------------------------------------------------

_DJI_TS_RE = re.compile(r"(\d{8}T\d{6})")


def _parse_wall_time(filename: str) -> datetime:
    """Extract wall-clock UTC start time from DJI filename.

    DJI records in the format TX01_MIC020_20260426T113310_orig.ogg.
    The timestamp component (YYYYMMDDTHHMMSS) is UTC.

    Returns datetime at UTC midnight if no timestamp found (files will still
    sort by filename lexicographically, which is also chronological for DJI).
    """
    m = _DJI_TS_RE.search(filename)
    if m:
        ts_str = m.group(1)
        return datetime.strptime(ts_str, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    logger.warning("No timestamp in filename %r — using epoch for sort", filename)
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------


def _s3_key_exists(s3_client: Any, bucket: str, key: str) -> bool:
    """Return True if the S3 key exists (HEAD request)."""
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:
        if "404" in str(exc) or "NoSuchKey" in str(exc) or "Not Found" in str(exc):
            return False
        raise


def _upload(s3_client: Any, bucket: str, key: str, local_path: Path) -> str:
    """Upload a local file to S3. Returns the s3:// URI."""
    s3_client.upload_file(str(local_path), bucket, key)
    logger.info("Uploaded s3://%s/%s", bucket, key)
    return f"s3://{bucket}/{key}"


def _put_json(s3_client: Any, bucket: str, key: str, data: Any) -> str:
    """Serialize data to JSON and PUT to S3. Returns the s3:// URI."""
    body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    s3_client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    logger.info("Uploaded s3://%s/%s", bucket, key)
    return f"s3://{bucket}/{key}"


def _put_text(s3_client: Any, bucket: str, key: str, text: str) -> str:
    """PUT UTF-8 text to S3. Returns the s3:// URI."""
    s3_client.put_object(Bucket=bucket, Key=key, Body=text.encode("utf-8"), ContentType="text/markdown")
    logger.info("Uploaded s3://%s/%s", bucket, key)
    return f"s3://{bucket}/{key}"


def _download(s3_client: Any, bucket: str, key: str, dest: Path) -> Path:
    """Download an S3 object to a local path."""
    s3_client.download_file(bucket, key, str(dest))
    return dest


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------


def unify_audio(
    session_id: str,
    s3_bucket: str,
    speaker_mapping: dict[str, str],
    enhancements: dict[str, bool] | None = None,
    *,
    s3_client: Any | None = None,
    local_raw_prefix: str | None = None,
) -> MasterOutput:
    """Top-level orchestrator: download, filter, mix, transcribe, QC, upload.

    Idempotent: checks S3 for each output before running expensive steps.
    All intermediate files are cached in a local temp directory. Tier 2 may
    extend this function by passing the temp dir explicitly (future API).

    Args:
        session_id: Unique session identifier (e.g. "ep-2026-04-26-josh-cashman").
            Used as both the S3 prefix under raw/ and outputs/.
        s3_bucket: S3 bucket name (e.g. "jerard-activefounder").
        speaker_mapping: Maps channel prefix to speaker name.
            Keys must match the TX-prefix of source filenames.
            Example: {"TX01": "Josh", "TX00": "Chris", "ambient": "David"}
        enhancements: Which Tier 1 filters to apply.
            Keys: "highpass", "denoise", "level_match", "bleed_cancel".
            Defaults to all enabled.
        s3_client: Optional pre-built boto3 S3 client. Created automatically
            if not provided (uses default credential chain).
        local_raw_prefix: S3 prefix where raw OGGs live.
            Defaults to "raw/{session_id}/dji/".

    Returns:
        MasterOutput with S3 URIs and metadata for all produced artifacts.
    """
    if enhancements is None:
        enhancements = {"highpass": True, "denoise": True, "level_match": True, "bleed_cancel": True}

    if s3_client is None:
        s3_client = boto3.client("s3", region_name="us-east-1")

    raw_prefix = local_raw_prefix or f"raw/{session_id}/dji/"
    out_prefix = f"outputs/{session_id}"

    # Define all output S3 keys upfront for idempotency checks
    keys = {
        "audio": f"{out_prefix}/master_basic.mp3",
        "transcript_md": f"{out_prefix}/master_transcript.md",
        "transcript_json": f"{out_prefix}/master_transcript.json",
        "chapters_json": f"{out_prefix}/master_chapters.json",
        "qc_json": f"{out_prefix}/master_qc.json",
    }

    # Check if final output already exists — if so, reconstruct and return
    if _s3_key_exists(s3_client, s3_bucket, keys["audio"]):
        logger.info("Master audio already exists for %s — loading existing outputs.", session_id)
        return _load_existing_output(s3_client, s3_bucket, keys, session_id, speaker_mapping)

    with tempfile.TemporaryDirectory(prefix=f"cp_master_{session_id}_") as tmp_str:
        tmp = Path(tmp_str)
        logger.info("Working directory: %s", tmp)

        # Step 1: List and download raw OGGs from S3
        channel_files = _fetch_channel_files(
            s3_client=s3_client,
            bucket=s3_bucket,
            prefix=raw_prefix,
            tmp=tmp,
        )

        if not channel_files:
            raise ValueError(f"No OGG files found at s3://{s3_bucket}/{raw_prefix}")

        logger.info("Found %d raw channel files", len(channel_files))

        # Step 2: Group by channel (TX prefix) and concat per-channel
        # S3-cached at master/concat_{channel}.ogg
        channel_map = _group_by_channel(channel_files, speaker_mapping)
        concat_paths: dict[str, Path] = {}
        for channel_key, files in channel_map.items():
            out = tmp / f"concat_{channel_key}.ogg"
            if _audio_cache_get(s3_client, s3_bucket, session_id, "concat", channel_key, out, "ogg"):
                logger.info("Concatenated %s: CACHED -> %s", channel_key, out.name)
            else:
                _concat_per_channel(files, out)
                logger.info("Concatenated %s: %d files -> %s", channel_key, len(files), out.name)
                _audio_cache_put(s3_client, s3_bucket, session_id, "concat", channel_key, out, "ogg")
            concat_paths[channel_key] = out

        # Step 3: Apply Tier 1 filters per channel
        # S3-cached at master/filtered_{channel}.ogg
        filtered_paths: dict[str, Path] = {}
        for channel_key, concat_path in concat_paths.items():
            out = tmp / f"filtered_{channel_key}.ogg"
            if _audio_cache_get(s3_client, s3_bucket, session_id, "filtered", channel_key, out, "ogg"):
                logger.info("Filtered %s: CACHED -> %s", channel_key, out.name)
            else:
                _apply_tier1_filters(concat_path, out, enhancements)
                _audio_cache_put(s3_client, s3_bucket, session_id, "filtered", channel_key, out, "ogg")
            filtered_paths[channel_key] = out

        # Step 4: Optional bleed cancellation (only when exactly 2 channels)
        # FIX: previously wrote PCM_16 audio to .ogg-named files, which soundfile
        # rejects ("Invalid combination of format, subtype and endian"). Use .wav.
        # S3-cached at master/debled_{channel}.wav
        if enhancements.get("bleed_cancel") and len(filtered_paths) == 2:
            keys_list = list(filtered_paths.keys())
            ch_a_key, ch_b_key = keys_list[0], keys_list[1]
            out_a = tmp / f"debled_{ch_a_key}.wav"
            out_b = tmp / f"debled_{ch_b_key}.wav"
            cached_a = _audio_cache_get(s3_client, s3_bucket, session_id, "debled", ch_a_key, out_a, "wav")
            cached_b = _audio_cache_get(s3_client, s3_bucket, session_id, "debled", ch_b_key, out_b, "wav")
            if cached_a and cached_b:
                logger.info("Bleed cancel: BOTH CACHED -> %s, %s", out_a.name, out_b.name)
            else:
                _bleed_cancel(
                    filtered_paths[ch_a_key],
                    filtered_paths[ch_b_key],
                    out_a,
                    out_b,
                )
                _audio_cache_put(s3_client, s3_bucket, session_id, "debled", ch_a_key, out_a, "wav")
                _audio_cache_put(s3_client, s3_bucket, session_id, "debled", ch_b_key, out_b, "wav")
            filtered_paths[ch_a_key] = out_a
            filtered_paths[ch_b_key] = out_b

        # Step 5: Mix to mono MP3
        master_mp3 = tmp / "master_basic.mp3"
        channel_path_list = list(filtered_paths.values())
        _mix_channels(channel_path_list, master_mp3, mode="mono")

        # Step 6: Get master duration
        duration_sec = _probe_duration(master_mp3)

        # Step 7: Load and merge per-channel transcripts from S3
        transcript_prefix = f"transcripts/{session_id}/"
        merged = _load_and_merge_transcripts(
            s3_client=s3_client,
            bucket=s3_bucket,
            prefix=transcript_prefix,
            speaker_mapping=speaker_mapping,
            channel_map=channel_map,
        )

        # Step 8: Emit transcript markdown
        transcript_md_path = tmp / "master_transcript.md"
        _emit_transcript_md(merged, transcript_md_path)

        # Step 9: Generate chapters
        chapters = _generate_chapters(merged)

        # Step 10: QC checks
        qc = _qc_checks(
            channel_paths=list(filtered_paths.values()),
            master_path=master_mp3,
            merged=merged,
        )

        # Step 11: Upload all artifacts
        audio_uri = _upload(s3_client, s3_bucket, keys["audio"], master_mp3)
        transcript_md_uri = _put_text(s3_client, s3_bucket, keys["transcript_md"],
                                       transcript_md_path.read_text(encoding="utf-8"))
        transcript_json_uri = _put_json(s3_client, s3_bucket, keys["transcript_json"], merged)
        chapters_uri = _put_json(s3_client, s3_bucket, keys["chapters_json"], chapters)
        qc_uri = _put_json(s3_client, s3_bucket, keys["qc_json"], qc)

        speakers_detected = sorted({seg["speaker"] for seg in merged.get("segments", [])})

        logger.info(
            "Unification complete for %s: %.1f min, %d speakers, drift=%.0f ms",
            session_id,
            duration_sec / 60,
            len(speakers_detected),
            qc.get("sync_drift_ms", 0),
        )

        return MasterOutput(
            audio_s3_uri=audio_uri,
            transcript_md_s3_uri=transcript_md_uri,
            transcript_json_s3_uri=transcript_json_uri,
            chapters_json_s3_uri=chapters_uri,
            qc_json_s3_uri=qc_uri,
            duration_sec=duration_sec,
            speakers_detected=speakers_detected,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_existing_output(
    s3_client: Any,
    bucket: str,
    keys: dict[str, str],
    session_id: str,
    speaker_mapping: dict[str, str],
) -> MasterOutput:
    """Reconstruct MasterOutput from already-uploaded S3 artifacts."""
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=keys["transcript_json"])
        merged = json.loads(obj["Body"].read())
        speakers = sorted({seg["speaker"] for seg in merged.get("segments", [])})
    except Exception:
        speakers = list(speaker_mapping.values())

    try:
        qc_obj = s3_client.get_object(Bucket=bucket, Key=keys["qc_json"])
        qc = json.loads(qc_obj["Body"].read())
        duration = qc.get("master_duration_sec", 0.0)
    except Exception:
        duration = 0.0

    return MasterOutput(
        audio_s3_uri=f"s3://{bucket}/{keys['audio']}",
        transcript_md_s3_uri=f"s3://{bucket}/{keys['transcript_md']}",
        transcript_json_s3_uri=f"s3://{bucket}/{keys['transcript_json']}",
        chapters_json_s3_uri=f"s3://{bucket}/{keys['chapters_json']}",
        qc_json_s3_uri=f"s3://{bucket}/{keys['qc_json']}",
        duration_sec=duration,
        speakers_detected=speakers,
    )


def _fetch_channel_files(
    s3_client: Any,
    bucket: str,
    prefix: str,
    tmp: Path,
) -> list[Path]:
    """List and download all OGG files from S3 prefix. Returns local paths."""
    paginator = s3_client.get_paginator("list_objects_v2")
    local_paths: list[Path] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.lower().endswith(".ogg"):
                continue
            filename = Path(key).name
            dest = tmp / filename
            if not dest.exists():
                logger.info("Downloading %s", key)
                _download(s3_client, bucket, key, dest)
            local_paths.append(dest)
    return local_paths


def _group_by_channel(
    files: list[Path],
    speaker_mapping: dict[str, str],
) -> dict[str, list[Path]]:
    """Group files by channel key (TX prefix) and sort chronologically.

    Files not matching any speaker_mapping key are grouped under "ambient"
    if "ambient" is in the mapping, otherwise skipped with a warning.

    Returns: {channel_key: [sorted Path list]}
    """
    groups: dict[str, list[Path]] = {}
    for f in files:
        matched = None
        for prefix_key in speaker_mapping:
            if prefix_key == "ambient":
                continue
            if f.name.startswith(prefix_key):
                matched = prefix_key
                break
        if matched is None:
            if "ambient" in speaker_mapping:
                matched = "ambient"
            else:
                logger.warning("File %s does not match any speaker_mapping key — skipping", f.name)
                continue
        groups.setdefault(matched, []).append(f)

    # Sort each channel's files by wall-clock timestamp embedded in filename
    for key in groups:
        groups[key].sort(key=lambda p: _parse_wall_time(p.name))

    return groups


def _probe_duration(path: Path) -> float:
    """Return duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Core audio functions
# ---------------------------------------------------------------------------


def _concat_per_channel(channel_files: list[Path], out_path: Path) -> None:
    """Concatenate per-channel OGGs in chronological order using ffmpeg concat demuxer.

    Uses the concat demuxer (file list approach) rather than the concat filter
    because the demuxer is lossless and handles discontinuous timestamps
    correctly — each DJI file may have a gap between it and the next one,
    which the demuxer bridges without re-encoding.

    Args:
        channel_files: Paths sorted in chronological order (caller's responsibility).
        out_path: Destination file path.
    """
    # Build the concat list file
    list_file = out_path.with_suffix(".concat_list.txt")
    lines = [f"file '{p.as_posix()}'" for p in channel_files]
    list_file.write_text("\n".join(lines), encoding="utf-8")

    # Re-encode to opus rather than copy: the concat demuxer + -c copy on opus/ogg
    # has timestamp discontinuity issues across segment boundaries that cause
    # soundfile to read only the first segment's duration. Re-encoding is slightly
    # slower but produces a clean single-stream output file.
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c:a", "libopus",
        "-b:a", "96k",
        str(out_path),
    ]
    _run_ffmpeg(cmd, context=f"concat {out_path.name}")


def _apply_tier1_filters(in_path: Path, out_path: Path, filters: dict[str, bool]) -> None:
    """Apply Tier 1 audio enhancement chain in a single ffmpeg invocation.

    Filter chain (in order):
    1. highpass=f=80 — removes low-frequency handling noise and wind rumble
       below the fundamental human vocal range.
    2. afftdn=nf=-25 — FFT-based noise reduction. arnndn (neural) is preferred
       but requires an external model file not bundled with ffmpeg Windows builds.
       afftdn with nf=-25 dB noise floor estimate is equivalent for most speech.
       Decision: afftdn primary, arnndn gated by model availability (future Tier 2).
    3. loudnorm=I=-23:TP=-1.5:LRA=7 — EBU R128 loudness normalization to -23 LUFS.
       Two-pass loudnorm would be more accurate but requires writing to null first;
       single-pass is sufficient for podcast distribution (-23 LUFS is the target).

    Args:
        in_path: Source audio file.
        out_path: Filtered output file.
        filters: Dict of filter name -> bool. Skipped if value is False.
    """
    filter_parts: list[str] = []
    if filters.get("highpass"):
        filter_parts.append("highpass=f=80")
    if filters.get("denoise"):
        filter_parts.append("afftdn=nf=-25")
    if filters.get("level_match"):
        filter_parts.append("loudnorm=I=-23:TP=-1.5:LRA=7")

    if not filter_parts:
        # No filters requested — just copy
        import shutil
        shutil.copy2(in_path, out_path)
        return

    af = ",".join(filter_parts)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(in_path),
        "-af", af,
        "-c:a", "libopus",
        "-b:a", "96k",
        str(out_path),
    ]
    _run_ffmpeg(cmd, context=f"filter {out_path.name}")


def _bleed_cancel(channel_a: Path, channel_b: Path, out_a: Path, out_b: Path) -> None:
    """Reference-based bleed cancellation for two-channel recordings.

    When a speaker is dominant on channel A (lavalier), their voice bleeds into
    channel B (the other speaker's mic) due to physical proximity. This function
    attenuates that bleed using cross-correlation to estimate the lag and scale.

    Algorithm (chunk-based):
    1. Divide both channels into 1-second chunks.
    2. For each chunk, identify the "dominant" channel (higher RMS power).
    3. Compute cross-correlation between dominant and non-dominant channel
       over a ±100 ms window to find the bleed lag.
    4. Subtract a lag-aligned, scaled copy of the dominant channel from the
       non-dominant channel. Scale is estimated from the peak cross-correlation.
    5. Reconstruct the debled signal and write output.

    Limitations (for Tier 2 to improve upon):
    - Cross-correlation lag is noisy in reverberant environments.
    - The scale estimate is coarse (no adaptive filter / LMS here).
    - Works well for physically separated speakers (David in ambient mic vs
      Josh/Chris on lavaliers) but less effective for co-located speakers.

    Args:
        channel_a: Path to first channel (processed OGG).
        channel_b: Path to second channel (processed OGG).
        out_a: Output path for debled channel A.
        out_b: Output path for debled channel B.
    """
    import soundfile as sf

    # Load both channels as float32
    data_a, sr_a = sf.read(str(channel_a), dtype="float32", always_2d=False)
    data_b, sr_b = sf.read(str(channel_b), dtype="float32", always_2d=False)

    if sr_a != sr_b:
        logger.warning(
            "Sample rate mismatch: %d vs %d — skipping bleed cancel", sr_a, sr_b
        )
        import shutil
        shutil.copy2(channel_a, out_a)
        shutil.copy2(channel_b, out_b)
        return

    sr = sr_a

    # Pad shorter channel to same length
    max_len = max(len(data_a), len(data_b))
    if len(data_a) < max_len:
        data_a = np.concatenate([data_a, np.zeros(max_len - len(data_a), dtype=np.float32)])
    if len(data_b) < max_len:
        data_b = np.concatenate([data_b, np.zeros(max_len - len(data_b), dtype=np.float32)])

    chunk_samples = sr  # 1-second chunks
    max_lag_samples = int(sr * 0.1)  # ±100 ms lag search window
    out_a_data = data_a.copy()
    out_b_data = data_b.copy()

    n_chunks = max_len // chunk_samples
    for i in range(n_chunks):
        start = i * chunk_samples
        end = start + chunk_samples
        chunk_a = data_a[start:end]
        chunk_b = data_b[start:end]

        rms_a = float(np.sqrt(np.mean(chunk_a ** 2)))
        rms_b = float(np.sqrt(np.mean(chunk_b ** 2)))

        # Only cancel if one channel is clearly dominant (>6 dB louder)
        if rms_a < 1e-6 or rms_b < 1e-6:
            continue
        ratio = rms_a / rms_b
        if ratio < 2.0 and ratio > 0.5:
            continue  # Too similar — skip to avoid over-cancellation

        if ratio >= 2.0:
            dominant, subdominant = chunk_a, chunk_b
            out_target = out_b_data
        else:
            dominant, subdominant = chunk_b, chunk_a
            out_target = out_a_data

        # Cross-correlate to find bleed lag
        xcorr = np.correlate(subdominant, dominant, mode="full")
        center = len(xcorr) // 2
        window = xcorr[center - max_lag_samples: center + max_lag_samples + 1]
        lag = int(np.argmax(np.abs(window))) - max_lag_samples

        # Estimate scale from peak correlation normalized by dominant power
        dom_power = float(np.dot(dominant, dominant))
        if dom_power < 1e-10:
            continue
        scale = float(np.asarray(xcorr[center + lag]).item()) / dom_power
        # Clamp scale to prevent over-subtraction
        scale = max(-0.9, min(0.9, scale))

        # Build lag-shifted dominant signal
        if lag >= 0:
            shifted = np.concatenate([np.zeros(lag, dtype=np.float32), dominant[:-lag if lag > 0 else None]])
        else:
            shifted = np.concatenate([dominant[-lag:], np.zeros(-lag, dtype=np.float32)])
        shifted = shifted[:chunk_samples]

        out_target[start:end] -= scale * shifted

    sf.write(str(out_a), out_a_data, sr, subtype="PCM_16")
    sf.write(str(out_b), out_b_data, sr, subtype="PCM_16")
    logger.info("Bleed cancel complete: wrote %s, %s", out_a.name, out_b.name)


def _mix_channels(channel_paths: list[Path], out_path: Path, mode: str = "mono") -> None:
    """Mix multiple channels into a single output using ffmpeg amix/amerge.

    Mode "mono":  Downmix all channels to single mono track (default for podcast).
                  Uses amix filter with equal weights. This is the expected
                  output for podcast distribution — listeners hear both speakers
                  in both ears at equal volume.

    Mode "stereo": Place first channel on L, second on R (Josh-L Chris-R).
                   Uses amerge. Useful for Auphonic / post-production workflows
                   that need speaker isolation preserved in the mix.

    Args:
        channel_paths: List of filtered/debled per-channel audio files.
        out_path: Destination MP3 path (Tier 1 output is always MP3).
        mode: "mono" or "stereo".
    """
    if len(channel_paths) == 1:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(channel_paths[0]),
            "-c:a", "libmp3lame",
            "-q:a", "2",  # ~190 kbps VBR
            "-ac", "1",
            str(out_path),
        ]
    elif mode == "stereo" and len(channel_paths) == 2:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(channel_paths[0]),
            "-i", str(channel_paths[1]),
            "-filter_complex", "amerge=inputs=2,aformat=channel_layouts=stereo",
            "-c:a", "libmp3lame",
            "-q:a", "2",
            str(out_path),
        ]
    else:
        # amix — handles N channels, mono output
        inputs = []
        for p in channel_paths:
            inputs += ["-i", str(p)]
        n = len(channel_paths)
        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", f"amix=inputs={n}:duration=longest:dropout_transition=0,aformat=channel_layouts=mono",
            "-c:a", "libmp3lame",
            "-q:a", "2",
            str(out_path),
        ]
    _run_ffmpeg(cmd, context=f"mix -> {out_path.name}")


# ---------------------------------------------------------------------------
# Transcript functions
# ---------------------------------------------------------------------------


def _load_and_merge_transcripts(
    s3_client: Any,
    bucket: str,
    prefix: str,
    speaker_mapping: dict[str, str],
    channel_map: dict[str, list[Path]],
) -> dict:
    """Download per-channel transcript JSONs from S3 and merge into a timeline.

    Transcript JSONs follow the Whisper verbose_json format:
    {"text": "...", "segments": [{"start": 0.0, "end": 2.3, "text": "..."}, ...]}

    Maps each JSON file to a channel by matching the source filename stem.
    Falls back to merging all available JSONs attributed to "Unknown" if
    mapping fails (so the transcript is never empty).
    """
    # List all JSON transcripts under the session prefix
    paginator = s3_client.get_paginator("list_objects_v2")
    channel_jsons: dict[str, dict] = {}  # channel_key -> whisper JSON

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            stem = Path(key).stem  # e.g. "TX01_MIC020_20260426T113310_orig"
            # Match to channel
            matched_channel = None
            for ch_key in channel_map:
                if ch_key == "ambient":
                    continue
                if stem.startswith(ch_key):
                    matched_channel = ch_key
                    break
            if matched_channel is None and "ambient" in speaker_mapping:
                matched_channel = "ambient"
            elif matched_channel is None:
                matched_channel = "unknown"

            try:
                body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
                data = json.loads(body)
                # Merge multiple files per channel by appending segments with time offset
                if matched_channel in channel_jsons:
                    existing = channel_jsons[matched_channel]
                    max_t = max((s.get("end", 0.0) for s in existing.get("segments", [])), default=0.0)
                    for seg in data.get("segments", []):
                        existing["segments"].append({
                            "start": seg.get("start", 0.0) + max_t,
                            "end": seg.get("end", 0.0) + max_t,
                            "text": seg.get("text", ""),
                        })
                    existing["text"] = existing["text"] + " " + data.get("text", "")
                else:
                    channel_jsons[matched_channel] = {
                        "text": data.get("text", ""),
                        "segments": [
                            {"start": s.get("start", 0.0), "end": s.get("end", 0.0), "text": s.get("text", "")}
                            for s in data.get("segments", [])
                        ],
                    }
            except Exception as exc:
                logger.warning("Failed to load transcript %s: %s", key, exc)

    return _merge_transcripts(channel_jsons, speaker_mapping)


def _merge_transcripts(channel_jsons: dict[str, dict], speaker_mapping: dict[str, str]) -> dict:
    """Interleave per-channel Whisper JSON outputs into a single timeline.

    Each Whisper JSON has per-segment start/end times relative to the start
    of that channel's audio. This function walks all segments across channels,
    interleaves them by start_time, and attributes each to the correct speaker
    via channel_key -> speaker_name mapping.

    The per-channel start times are relative to the beginning of that channel's
    concatenated audio, NOT to a shared wall clock. For Tier 1, this is
    acceptable — Tier 2 (WhisperX alignment + diarization) will produce
    wall-clock-anchored timestamps.

    Args:
        channel_jsons: {channel_key: {"segments": [{start, end, text}, ...]}}
        speaker_mapping: {"TX01": "Josh", "TX00": "Chris", "ambient": "David"}

    Returns:
        {"segments": [{start, end, speaker, text}, ...]} sorted by start time.
    """
    all_segments: list[dict] = []

    for channel_key, data in channel_jsons.items():
        speaker = speaker_mapping.get(channel_key, channel_key)
        for seg in data.get("segments", []):
            all_segments.append({
                "start": float(seg.get("start", 0.0)),
                "end": float(seg.get("end", 0.0)),
                "speaker": speaker,
                "text": seg.get("text", "").strip(),
            })

    # Sort by start time, break ties with speaker name for determinism
    all_segments.sort(key=lambda s: (s["start"], s["speaker"]))

    return {"segments": all_segments}


def _emit_transcript_md(merged: dict, out_path: Path) -> None:
    """Format the merged transcript as a human-readable Markdown file.

    Format:
        [HH:MM:SS UTC] **Speaker:** text

    Consecutive segments by the same speaker are grouped into paragraphs
    to improve readability. A paragraph break is inserted when the speaker
    changes or when there is a gap > 3 seconds between segments.

    Args:
        merged: {"segments": [{start, end, speaker, text}, ...]}
        out_path: Destination .md file path.
    """
    segments = merged.get("segments", [])
    lines: list[str] = ["# Transcript\n"]

    i = 0
    while i < len(segments):
        seg = segments[i]
        speaker = seg["speaker"]
        start_sec = seg["start"]
        hours = int(start_sec // 3600)
        minutes = int((start_sec % 3600) // 60)
        seconds = int(start_sec % 60)
        timestamp = f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"

        # Collect consecutive same-speaker segments (gap <= 3s)
        texts: list[str] = [seg["text"]]
        j = i + 1
        while j < len(segments):
            next_seg = segments[j]
            gap = next_seg["start"] - segments[j - 1]["end"]
            if next_seg["speaker"] == speaker and gap <= 3.0:
                texts.append(next_seg["text"])
                j += 1
            else:
                break

        paragraph = " ".join(t for t in texts if t)
        lines.append(f"{timestamp} **{speaker}:** {paragraph}\n")
        i = j

    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Chapters
# ---------------------------------------------------------------------------


def _generate_chapters(merged: dict, target_chapter_min: int = 10) -> dict:
    """Auto-detect chapter boundaries at ~10-minute intervals.

    Prefers to start chapters at speaker handoffs rather than mid-monologue,
    because a handoff is a natural topical break point. The algorithm:
    1. Walk segments looking for the first speaker change at or after each
       N-minute target mark.
    2. If no speaker change occurs within 2 minutes of the target, force
       a chapter at the target mark anyway.

    Args:
        merged: {"segments": [{start, end, speaker, text}, ...]}
        target_chapter_min: Target chapter length in minutes (default 10).

    Returns:
        {"chapters": [{"start_sec": float, "title": "Chapter N", "summary": ""}, ...]}
        The "summary" field is left empty for Tier 2 (LLM synthesis) to fill in.
    """
    segments = merged.get("segments", [])
    if not segments:
        return {"chapters": [{"start_sec": 0.0, "title": "Chapter 1", "summary": ""}]}

    target_interval_sec = target_chapter_min * 60
    tolerance_sec = 120  # Allow up to 2 min drift to hit a speaker change

    total_dur = segments[-1]["end"] if segments else 0.0
    chapters: list[dict] = [{"start_sec": 0.0, "title": "Chapter 1", "summary": ""}]

    next_target = target_interval_sec
    chapter_num = 2

    while next_target < total_dur - target_interval_sec * 0.5:
        # Find the best chapter cut point near next_target
        best_cut: float | None = None

        # First pass: look for a speaker change within the tolerance window
        for idx in range(1, len(segments)):
            seg = segments[idx]
            prev_seg = segments[idx - 1]
            if seg["start"] < next_target:
                continue
            if seg["start"] > next_target + tolerance_sec:
                break
            if seg["speaker"] != prev_seg["speaker"]:
                best_cut = seg["start"]
                break

        # Second pass: if no speaker change found, use the target itself
        if best_cut is None:
            # Find nearest segment start to next_target
            best_cut = next_target
            closest_dist = float("inf")
            for seg in segments:
                dist = abs(seg["start"] - next_target)
                if dist < closest_dist:
                    closest_dist = dist
                    best_cut = seg["start"]

        chapters.append({
            "start_sec": best_cut,
            "title": f"Chapter {chapter_num}",
            "summary": "",
        })
        chapter_num += 1
        next_target = best_cut + target_interval_sec

    return {"chapters": chapters}


# ---------------------------------------------------------------------------
# QC checks
# ---------------------------------------------------------------------------


def _qc_checks(
    channel_paths: list[Path],
    master_path: Path,
    merged: dict,
) -> dict:
    """Produce a QC report for the unification run.

    Checks performed:
    - sync_drift_ms: Cross-correlate first 10s of each channel pair at 3 random
      offsets to estimate the maximum timing offset between channels. Values
      above ~500ms indicate a sync issue worth investigating before Tier 2.
    - duration_match: Compare master duration to the expected duration based on
      the sum of per-channel lengths. Mismatch > 5s warrants investigation.
    - snr_per_channel: Estimate SNR by comparing the RMS power of the first
      loud segment to the mean RMS of silence segments (defined as segments
      where RMS < 5% of the channel maximum).
    - transcript_alignment: Placeholder. Tier 2 (WhisperX) will produce real
      word-level alignment. Here we report the segment count as a proxy.

    Args:
        channel_paths: Per-channel filtered audio paths.
        master_path: The final mixed MP3.
        merged: The merged transcript dict.

    Returns:
        QC dict. All values are informational — unify_audio does not gate on them.
    """
    import soundfile as sf

    qc: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sync_drift_ms": 0.0,
        "duration_match": True,
        "master_duration_sec": 0.0,
        "transcript_alignment": {
            "segment_count": len(merged.get("segments", [])),
            "note": "Word-level alignment requires Tier 2 WhisperX",
        },
        "snr_per_channel": {},
    }

    # Master duration
    master_dur = _probe_duration(master_path)
    qc["master_duration_sec"] = master_dur

    # SNR per channel
    for ch_path in channel_paths:
        try:
            info = sf.info(str(ch_path))
            sr = info.samplerate
            data, sr = sf.read(str(ch_path), dtype="float32", always_2d=False, stop=sr * 30)
            # Rough SNR: signal RMS in loudest 1s vs mean RMS of quiet 1s windows
            chunk = sr  # 1-second windows
            rms_vals = [
                float(np.sqrt(np.mean(data[i:i + chunk] ** 2)))
                for i in range(0, min(len(data), sr * 30), chunk)
            ]
            if rms_vals:
                max_rms = max(rms_vals)
                quiet_rms = [r for r in rms_vals if r < max_rms * 0.05]
                noise_floor = float(np.mean(quiet_rms)) if quiet_rms else 1e-6
                snr_db = 20 * np.log10(max_rms / max(noise_floor, 1e-6))
                qc["snr_per_channel"][ch_path.stem] = round(float(snr_db), 1)
        except Exception as exc:
            logger.warning("SNR check failed for %s: %s", ch_path.name, exc)
            qc["snr_per_channel"][ch_path.stem] = None

    # Sync drift (cross-correlate first 10s of first two channels)
    # Uses normalized cross-correlation to find the most likely lag between channels.
    # A high lag indicates the two DJI recorders started at different times, which
    # would cause the speakers to be out-of-sync in the mixed output.
    if len(channel_paths) >= 2:
        try:
            import soundfile as sf

            # Read actual sample rate from file first, then limit to 10s
            info_a = sf.info(str(channel_paths[0]))
            info_b = sf.info(str(channel_paths[1]))
            sr_a = info_a.samplerate
            sr_b = info_b.samplerate

            data_a, _ = sf.read(str(channel_paths[0]), dtype="float32",
                                always_2d=False, stop=sr_a * 10)
            data_b, _ = sf.read(str(channel_paths[1]), dtype="float32",
                                always_2d=False, stop=sr_b * 10)

            min_len = min(len(data_a), len(data_b))
            if min_len > 100:
                data_a = data_a[:min_len]
                data_b = data_b[:min_len]
                # Normalize to avoid scale differences dominating the correlation
                std_a = data_a.std()
                std_b = data_b.std()
                if std_a > 1e-6 and std_b > 1e-6:
                    norm_a = (data_a - data_a.mean()) / std_a
                    norm_b = (data_b - data_b.mean()) / std_b
                    xcorr = np.correlate(norm_a, norm_b, mode="full")
                    center = len(xcorr) // 2
                    max_lag_samples = min(sr_a, min_len // 2)  # ±1 second or half-length
                    lo = max(0, center - max_lag_samples)
                    hi = min(len(xcorr), center + max_lag_samples + 1)
                    window = xcorr[lo:hi]
                    lag_samples = int(np.argmax(np.abs(window))) - (center - lo)
                    drift_ms = abs(lag_samples) / sr_a * 1000
                    qc["sync_drift_ms"] = round(drift_ms, 1)
        except Exception as exc:
            logger.warning("Sync drift check failed: %s", exc)

    # Duration match: check if master is within 10s of longest channel
    try:
        channel_durs = [_probe_duration(p) for p in channel_paths]
        max_channel_dur = max(channel_durs) if channel_durs else 0.0
        qc["duration_match"] = abs(master_dur - max_channel_dur) < 10.0
        qc["channel_durations_sec"] = {p.stem: round(d, 1) for p, d in zip(channel_paths, channel_durs)}
    except Exception as exc:
        logger.warning("Duration match check failed: %s", exc)

    return qc


# ---------------------------------------------------------------------------
# ffmpeg runner
# ---------------------------------------------------------------------------


def _run_ffmpeg(cmd: list[str], context: str = "") -> None:
    """Run an ffmpeg command, raising RuntimeError on non-zero exit."""
    logger.debug("ffmpeg [%s]: %s", context, " ".join(cmd[:6]) + " ...")
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")[-500:]
        raise RuntimeError(f"ffmpeg failed [{context}] (exit {result.returncode}): {stderr}")
