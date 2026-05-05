# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Phase 1.5 — ECAPA fingerprinting GPU spot worker.

Lifecycle:
    1. Read spec.json from S3 URI in env var PHASE1_5_SPEC_S3_URI
    2. Download unified_transcript.json + raw_sources.json
    3. Download all audio files referenced in the unified transcript
    4. Load ECAPA-TDNN model (speechbrain, CUDA float32)
    5. Run speaker_fingerprint.run_fingerprinting() end-to-end
    6. Upload FingerprintResult JSON + speaker-labeled UnifiedTranscript JSON
    7. Drop _DONE marker (or _FAILED with traceback) and self-terminate

Designed for g6.xlarge spot (NVIDIA L4). speechbrain ECAPA RTF on L4 is
~0.05, so 3.5 hr of audio embeds in ~10 min of GPU time. Audio download +
ffmpeg windowing is typically the bottleneck.
"""

from __future__ import annotations

# Cap C-extension thread pools (skill rule G; ffmpeg is the I/O bottleneck so
# we leave more headroom for ffmpeg subprocess threads on a 4-vCPU box)
import os

os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")

import json  # noqa: E402
import logging  # noqa: E402
import posixpath  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
from pathlib import Path  # noqa: E402

import boto3  # noqa: E402


def _cross_platform_basename(path_str: str) -> str:
    """Return basename of a path string regardless of source OS path separators."""
    return posixpath.basename(path_str.replace("\\", "/"))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("phase1-5-fingerprint-worker")

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
SPEC_S3_URI = os.environ.get("PHASE1_5_SPEC_S3_URI", "")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """s3://bucket/key → (bucket, key)."""
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"Not an S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _get_instance_id() -> str | None:
    """IMDSv2 instance-id fetch."""
    try:
        req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            token = r.read().decode()
        req2 = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/instance-id",
            headers={"X-aws-ec2-metadata-token": token},
        )
        with urllib.request.urlopen(req2, timeout=3) as r:
            return r.read().decode()
    except Exception as exc:
        logger.warning("IMDS instance-id fetch failed: %s", exc)
        return None


def _self_terminate() -> None:
    iid = _get_instance_id()
    if iid:
        try:
            ec2 = boto3.client("ec2", region_name=REGION)
            ec2.terminate_instances(InstanceIds=[iid])
            logger.info("Self-terminate sent for %s", iid)
            time.sleep(30)
        except Exception as exc:
            logger.warning("terminate-instances failed: %s", exc)
    try:
        subprocess.run(["shutdown", "-h", "now"], check=False)
    except Exception:
        pass


def _key_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:
        if "404" in str(exc) or "NoSuchKey" in str(exc) or "Not Found" in str(exc):
            return False
        raise


def main() -> int:
    if not SPEC_S3_URI:
        logger.error("PHASE1_5_SPEC_S3_URI env var is required")
        return 1

    logger.info("Phase 1.5 fingerprint worker starting; spec=%s", SPEC_S3_URI)
    s3 = boto3.client("s3", region_name=REGION)

    spec_bucket, spec_key = _parse_s3_uri(SPEC_S3_URI)
    spec_body = s3.get_object(Bucket=spec_bucket, Key=spec_key)["Body"].read()
    spec = json.loads(spec_body)
    logger.info(
        "Loaded spec: container=%s, %d audio keys",
        spec["container"],
        len(spec["audio_s3_keys"]),
    )

    rs_bucket = spec.get("s3_bucket", spec_bucket)
    output_prefix = spec["output_prefix"].rstrip("/")
    container = spec["container"]

    # Skip if already done (idempotent)
    done_key = f"{output_prefix}/_DONE"
    if _key_exists(s3, rs_bucket, done_key):
        logger.info("Already complete: %s", done_key)
        return 0

    # Local imports here so they happen after env-var thread caps are set
    from contextpulse_pipeline.raw_source import RawSourceCollection  # noqa: E402
    from contextpulse_pipeline.speaker_fingerprint import (  # noqa: E402
        ECAPAExtractor,
        assign_speakers_to_unified,
        run_fingerprinting,
    )
    from contextpulse_pipeline.unified_transcript import (  # noqa: E402
        UnifiedSegment,
        UnifiedTranscript,
    )

    # Download raw_sources.json
    rs_key = spec["raw_sources_s3_key"]
    rs_body = s3.get_object(Bucket=rs_bucket, Key=rs_key)["Body"].read().decode("utf-8")
    coll = RawSourceCollection.from_json(rs_body)

    # Download unified_transcript.json
    ut_key = spec["unified_transcript_s3_key"]
    ut_body = s3.get_object(Bucket=rs_bucket, Key=ut_key)["Body"].read().decode("utf-8")
    ut_data = json.loads(ut_body)
    # Reconstitute UnifiedTranscript from JSON
    from datetime import datetime as _dt

    segments = []
    for s in ut_data.get("segments", []):
        segments.append(
            UnifiedSegment(
                wall_start_utc=_dt.fromisoformat(s["wall_start_utc"]),
                wall_end_utc=_dt.fromisoformat(s["wall_end_utc"]),
                source_sha256=s["source_sha256"],
                source_filename=s["source_filename"],
                source_tier=s["source_tier"],
                text=s["text"],
                avg_logprob=float(s.get("avg_logprob", 0.0)),
                speaker_label=s.get("speaker_label"),
            )
        )
    unified = UnifiedTranscript(
        container=ut_data["container"],
        anchor_origination_utc=_dt.fromisoformat(ut_data["anchor_origination_utc"]),
        segments=segments,
        unreachable_sources=list(ut_data.get("unreachable_sources", [])),
        missing_transcripts=list(ut_data.get("missing_transcripts", [])),
    )
    logger.info(
        "Reconstituted unified transcript: %d segments across %d sources",
        len(unified.segments),
        unified.n_sources,
    )

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        audio_dir = td_path / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        # Download all audio files; build sha256 → local Path mapping
        audio_paths: dict[str, Path] = {}
        by_basename = {_cross_platform_basename(rs.file_path): rs for rs in coll.sources}
        for audio_key in spec["audio_s3_keys"]:
            basename = Path(audio_key).name
            rs = by_basename.get(basename)
            if rs is None:
                logger.warning("No RawSource matches %s — skipping", basename)
                continue
            local = audio_dir / basename
            logger.info("Downloading %s -> %s", audio_key, local)
            s3.download_file(rs_bucket, audio_key, str(local))
            audio_paths[rs.sha256] = local

        if not audio_paths:
            logger.error("No audio files downloaded — abort")
            _upload_failure(s3, rs_bucket, output_prefix, "no audio files downloaded")
            return 2

        # Load ECAPA on GPU
        logger.info("Loading ECAPAExtractor (this triggers speechbrain import)")
        t0 = time.time()
        extractor = ECAPAExtractor(
            model_source=spec.get("model_source", "speechbrain/spkrec-ecapa-voxceleb"),
        )
        # Warm up the model with a 1-sec zero-vector pass
        import numpy as np  # noqa: E402

        _ = extractor.embed(np.zeros(16000, dtype=np.float32), sample_rate=16000)
        logger.info("ECAPA loaded + warmed in %.1f sec on %s", time.time() - t0, extractor.device)

        # Run end-to-end
        t0 = time.time()
        result = run_fingerprinting(
            unified,
            audio_paths,
            extractor,
            distance_threshold=float(spec.get("distance_threshold", 0.5)),
            min_chunk_sec=float(spec.get("min_chunk_sec", 2.0)),
            target_chunk_sec=float(spec.get("target_chunk_sec", 4.0)),
            max_clusters=spec.get("max_clusters"),
        )
        elapsed = time.time() - t0
        logger.info(
            "Fingerprinting complete in %.1f sec: %d chunks, %d speakers",
            elapsed,
            len(result.chunks),
            result.n_speakers,
        )

        # Apply speaker labels back onto the unified transcript
        labeled_unified = assign_speakers_to_unified(unified, result)

        # Upload outputs
        result_path = td_path / "fingerprint_result.json"
        result.to_json(path=result_path)
        labeled_path = td_path / "unified_transcript_labeled.json"
        labeled_unified.to_json(path=labeled_path)

        s3.upload_file(str(result_path), rs_bucket, f"{output_prefix}/fingerprint_result.json")
        s3.upload_file(
            str(labeled_path),
            rs_bucket,
            f"{output_prefix}/unified_transcript_labeled.json",
        )
        logger.info("Uploaded outputs to s3://%s/%s/", rs_bucket, output_prefix)

    # Completion marker
    summary = {
        "container": container,
        "n_chunks": len(result.chunks),
        "n_speakers": result.n_speakers,
        "elapsed_sec": elapsed,
    }
    s3.put_object(Bucket=rs_bucket, Key=done_key, Body=json.dumps(summary).encode("utf-8"))
    logger.info("All sources fingerprinted; _DONE marker written")
    return 0


def _upload_failure(s3, bucket: str, output_prefix: str, msg: str) -> None:
    body = {
        "error": msg,
        "traceback": traceback.format_exc() if sys.exc_info()[0] else None,
    }
    try:
        s3.put_object(
            Bucket=bucket,
            Key=f"{output_prefix}/_FAILED",
            Body=json.dumps(body).encode("utf-8"),
        )
    except Exception as exc:
        logger.error("Failed to upload failure marker: %s", exc)


if __name__ == "__main__":
    rc = 2
    try:
        rc = main()
    except Exception:
        logger.exception("Worker crashed")
        # Best-effort failure marker upload
        try:
            spec_bucket, spec_key = _parse_s3_uri(SPEC_S3_URI)
            s3 = boto3.client("s3", region_name=REGION)
            spec_body = s3.get_object(Bucket=spec_bucket, Key=spec_key)["Body"].read()
            spec = json.loads(spec_body)
            output_prefix = spec.get("output_prefix", "phase1-5-output/unknown").rstrip("/")
            rs_bucket = spec.get("s3_bucket", spec_bucket)
            _upload_failure(s3, rs_bucket, output_prefix, "worker crashed; see traceback")
        except Exception:
            logger.exception("Could not upload failure marker")
    finally:
        _self_terminate()
    sys.exit(rc)
