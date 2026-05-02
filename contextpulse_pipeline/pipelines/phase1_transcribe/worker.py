# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Phase 1 transcribe-only spot worker.

Lifecycle:
    1. Read spec.json from S3 URI in env var PHASE1_SPEC_S3_URI
    2. Download raw_sources.json
    3. For each audio_s3_key:
         a. Skip if {output_prefix}/{sha16}.json already exists in S3
         b. Download audio to /tmp
         c. Call transcribe_per_source.transcribe_raw_source() with the GPU backend
         d. Upload .json + .txt to S3 output_prefix
    4. Upload {output_prefix}/_DONE marker
    5. Self-terminate the EC2 instance

Designed for a g6.xlarge spot instance with NVIDIA L4 GPU. The transcribe
backend uses faster-whisper on CUDA + float16 (much faster than CPU int8).
"""

from __future__ import annotations

# Cap C-extension thread pools (skill rule G; matters less on GPU but safe)
import os

os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")

import json  # noqa: E402
import logging  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
from pathlib import Path  # noqa: E402

import boto3  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("phase1-transcribe-worker")

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
SPEC_S3_URI = os.environ.get("PHASE1_SPEC_S3_URI", "")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """s3://bucket/key → (bucket, key)."""
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"Not an S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _gpu_faster_whisper(audio_path: Path, *, model: str = "large-v3") -> dict:
    """GPU backend for transcribe_raw_source. CUDA + float16 on L4."""
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    wm = WhisperModel(model, device="cuda", compute_type="float16")
    segments_iter, info = wm.transcribe(
        str(audio_path),
        beam_size=1,
        vad_filter=True,
        word_timestamps=False,
    )
    segments: list[dict] = []
    full_text_parts: list[str] = []
    for seg in segments_iter:
        segments.append(
            {
                "start": float(seg.start),
                "end": float(seg.end),
                "text": seg.text,
                "avg_logprob": float(seg.avg_logprob),
                "compression_ratio": float(seg.compression_ratio),
                "no_speech_prob": float(seg.no_speech_prob),
            }
        )
        full_text_parts.append(seg.text)
    return {
        "language": info.language,
        "duration": float(info.duration),
        "text": "".join(full_text_parts),
        "segments": segments,
    }


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
    sys.exit(0)


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
        logger.error("PHASE1_SPEC_S3_URI env var is required")
        return 1

    logger.info("Phase 1 transcribe worker starting; spec=%s", SPEC_S3_URI)
    s3 = boto3.client("s3", region_name=REGION)

    spec_bucket, spec_key = _parse_s3_uri(SPEC_S3_URI)
    spec_body = s3.get_object(Bucket=spec_bucket, Key=spec_key)["Body"].read()
    spec = json.loads(spec_body)
    logger.info(
        "Loaded spec: container=%s, %d audio keys", spec["container"], len(spec["audio_s3_keys"])
    )

    # Local imports here so they happen after env-var thread caps are set
    from contextpulse_pipeline.raw_source import RawSource, RawSourceCollection  # noqa: E402
    from contextpulse_pipeline.transcribe_per_source import transcribe_raw_source  # noqa: E402

    # Download raw_sources.json
    rs_bucket = spec.get("s3_bucket", spec_bucket)
    rs_key = spec["raw_sources_s3_key"]
    rs_body = s3.get_object(Bucket=rs_bucket, Key=rs_key)["Body"].read().decode("utf-8")
    coll = RawSourceCollection.from_json(rs_body)
    by_path_basename: dict[str, RawSource] = {Path(s.file_path).name: s for s in coll.sources}

    output_prefix = spec["output_prefix"].rstrip("/")
    model = spec.get("model", "large-v3")
    failures: list[str] = []

    for audio_key in spec["audio_s3_keys"]:
        audio_basename = Path(audio_key).name
        rs = by_path_basename.get(audio_basename)
        if rs is None:
            logger.warning("No RawSource matches %s — skipping", audio_basename)
            failures.append(audio_key)
            continue

        sha16 = rs.sha256[:16]
        out_json_key = f"{output_prefix}/{sha16}.json"
        out_txt_key = f"{output_prefix}/{sha16}.txt"

        if _key_exists(s3, rs_bucket, out_json_key):
            logger.info("Already done in S3: %s", out_json_key)
            continue

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            local_audio = td_path / audio_basename
            logger.info("Downloading %s -> %s", audio_key, local_audio)
            s3.download_file(rs_bucket, audio_key, str(local_audio))

            # Update RawSource.file_path to point at local copy for transcribe_raw_source
            rs_local = rs.model_copy(update={"file_path": str(local_audio)})

            t0 = time.time()
            try:
                json_path = transcribe_raw_source(
                    rs_local,
                    td_path / "transcripts",
                    model=model,
                    transcribe_func=lambda p, *, model=model: _gpu_faster_whisper(p, model=model),
                )
            except Exception as exc:
                logger.error("Transcribe failed for %s: %s", audio_basename, exc)
                failures.append(audio_key)
                continue

            elapsed = time.time() - t0
            logger.info(
                "Transcribed %s in %.1f sec (RTF %.3f, audio=%.1fs)",
                audio_basename,
                elapsed,
                elapsed / max(rs.duration_sec, 1.0),
                rs.duration_sec,
            )

            txt_path = json_path.with_suffix(".txt")
            s3.upload_file(str(json_path), rs_bucket, out_json_key)
            s3.upload_file(str(txt_path), rs_bucket, out_txt_key)
            logger.info("Uploaded %s + .txt", out_json_key)

    # Completion marker
    if failures:
        marker_body = json.dumps({"failures": failures}).encode("utf-8")
        s3.put_object(Bucket=rs_bucket, Key=f"{output_prefix}/_FAILED", Body=marker_body)
        logger.warning("Done with %d failure(s)", len(failures))
    else:
        s3.put_object(Bucket=rs_bucket, Key=f"{output_prefix}/_DONE", Body=b"ok")
        logger.info("All sources transcribed successfully")

    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        logger.exception("Worker crashed")
        rc = 2
    finally:
        _self_terminate()
    sys.exit(rc)
