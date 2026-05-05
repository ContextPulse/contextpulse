# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Stage 6 — Voice isolation GPU spot worker.

Lifecycle:
    1. Read spec.json from S3 URI in env var VOICE_ISOLATION_SPEC_S3_URI
    2. Download fingerprint_result.json + raw_sources.json
    3. Download all audio files referenced in raw_sources
    4. Load WeSepExtractor (speechbrain + wesep, CUDA float32)
    5. Run voice_isolation.extract_per_speaker_tracks() end-to-end
    6. Upload isolation_result.json + every isolated WAV
    7. Drop _DONE marker (or _FAILED with traceback) and self-terminate

Mirrors pipelines.phase1_5_fingerprint.worker. The only difference is what
gets loaded (WeSep instead of ECAPA) and what the orchestrator does
(extract_per_speaker_tracks instead of run_fingerprinting).
"""

from __future__ import annotations

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
    return posixpath.basename(path_str.replace("\\", "/"))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("voice-isolation-worker")

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
SPEC_S3_URI = os.environ.get("VOICE_ISOLATION_SPEC_S3_URI", "")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"Not an S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _get_instance_id() -> str | None:
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


def _upload_failure(s3, bucket: str, output_prefix: str, msg: str) -> None:
    body = {"error": msg, "traceback": traceback.format_exc() if sys.exc_info()[0] else None}
    try:
        s3.put_object(
            Bucket=bucket,
            Key=f"{output_prefix}/_FAILED",
            Body=json.dumps(body).encode("utf-8"),
        )
    except Exception as exc:
        logger.error("Failed to upload failure marker: %s", exc)


def main() -> int:
    if not SPEC_S3_URI:
        logger.error("VOICE_ISOLATION_SPEC_S3_URI env var is required")
        return 1

    logger.info("Voice isolation worker starting; spec=%s", SPEC_S3_URI)
    s3 = boto3.client("s3", region_name=REGION)

    spec_bucket, spec_key = _parse_s3_uri(SPEC_S3_URI)
    spec = json.loads(s3.get_object(Bucket=spec_bucket, Key=spec_key)["Body"].read())

    rs_bucket = spec.get("s3_bucket", spec_bucket)
    output_prefix = spec["output_prefix"].rstrip("/")
    container = spec["container"]

    done_key = f"{output_prefix}/_DONE"
    if _key_exists(s3, rs_bucket, done_key):
        logger.info("Already complete: %s", done_key)
        return 0

    from contextpulse_pipeline.raw_source import RawSourceCollection  # noqa: E402
    from contextpulse_pipeline.speaker_fingerprint import FingerprintResult  # noqa: E402
    from contextpulse_pipeline.voice_isolation import (  # noqa: E402
        WeSepExtractor,
        extract_per_speaker_tracks,
    )

    rs_body = s3.get_object(Bucket=rs_bucket, Key=spec["raw_sources_s3_key"])["Body"].read().decode("utf-8")
    coll = RawSourceCollection.from_json(rs_body)

    fp_body = s3.get_object(Bucket=rs_bucket, Key=spec["fingerprint_result_s3_key"])["Body"].read().decode("utf-8")
    fingerprint = FingerprintResult.from_json(fp_body)
    logger.info(
        "Loaded fingerprint: %d clusters, %d chunks",
        len(fingerprint.clusters),
        len(fingerprint.chunks),
    )

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        audio_dir = td_path / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        out_dir = td_path / "out"
        out_dir.mkdir(parents=True, exist_ok=True)

        audio_paths: dict[str, Path] = {}
        source_tiers: dict[str, str] = {}
        source_filenames: dict[str, str] = {}
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
            source_tiers[rs.sha256] = rs.source_tier
            source_filenames[rs.sha256] = basename

        if not audio_paths:
            _upload_failure(s3, rs_bucket, output_prefix, "no audio files downloaded")
            return 2

        logger.info("Loading WeSepExtractor")
        t0 = time.time()
        extractor = WeSepExtractor(
            model_source=spec.get("model_source", "Wespeaker/wespeaker-voxceleb-resnet34"),
        )
        # Warm up with the first cluster's centroid (also catches load errors early)
        if fingerprint.clusters:
            import numpy as np  # noqa: E402

            warmup_audio = np.zeros(16000, dtype=np.float32)
            try:
                extractor.extract(
                    warmup_audio,
                    fingerprint.clusters[0].centroid,
                    sample_rate=16000,
                )
            except Exception:
                logger.exception("Extractor warm-up failed")
                _upload_failure(s3, rs_bucket, output_prefix, "extractor warm-up failed")
                return 3
        logger.info("WeSep loaded + warmed in %.1f sec on %s", time.time() - t0, extractor.device)

        t0 = time.time()
        isolation = extract_per_speaker_tracks(
            fingerprint=fingerprint,
            audio_paths=audio_paths,
            extractor=extractor,
            output_dir=out_dir,
            container=container,
            source_tiers=source_tiers,
            source_filenames=source_filenames,
            top_k_sources_per_speaker=spec.get("top_k_sources_per_speaker"),
        )
        elapsed = time.time() - t0
        logger.info(
            "Isolation complete in %.1f sec: %d tracks across %d speakers",
            elapsed,
            isolation.n_tracks,
            len(isolation.speakers),
        )

        # Upload manifest + every isolated WAV
        result_path = td_path / "isolation_result.json"
        isolation.to_json(path=result_path)
        s3.upload_file(str(result_path), rs_bucket, f"{output_prefix}/isolation_result.json")

        for track in isolation.tracks:
            key = f"{output_prefix}/voice_isolation/{track.output_path.name}"
            s3.upload_file(str(track.output_path), rs_bucket, key)

    summary = {
        "container": container,
        "n_tracks": isolation.n_tracks,
        "speakers": isolation.speakers,
        "elapsed_sec": elapsed,
    }
    s3.put_object(Bucket=rs_bucket, Key=done_key, Body=json.dumps(summary).encode("utf-8"))
    logger.info("Voice isolation complete; _DONE marker written")
    return 0


if __name__ == "__main__":
    rc = 2
    try:
        rc = main()
    except Exception:
        logger.exception("Worker crashed")
        try:
            spec_bucket, spec_key = _parse_s3_uri(SPEC_S3_URI)
            s3 = boto3.client("s3", region_name=REGION)
            spec = json.loads(s3.get_object(Bucket=spec_bucket, Key=spec_key)["Body"].read())
            output_prefix = spec.get("output_prefix", "voice-isolation-output/unknown").rstrip("/")
            rs_bucket = spec.get("s3_bucket", spec_bucket)
            _upload_failure(s3, rs_bucket, output_prefix, "worker crashed; see traceback")
        except Exception:
            logger.exception("Could not upload failure marker")
    finally:
        _self_terminate()
    sys.exit(rc)
