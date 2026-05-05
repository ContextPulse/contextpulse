# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Stage 6 Voice Isolation — local submission orchestrator.

Single-instance variant of the spot pipeline pattern (mirrors phase1_5).
Cross-source merging runs LOCALLY after this — it's pure CPU.

Workflow:
    1. Upload pipeline code + boot script to S3
    2. Upload raw_sources.json + fingerprint_result.json + audio files
    3. Upload spec.json
    4. Launch one g6.xlarge spot instance with capacity-diversified fallback
    5. Poll for _DONE / _FAILED marker
    6. Download isolation_result.json + every isolated WAV
    7. Always terminate the instance at exit
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import boto3

from contextpulse_pipeline.pipelines._spot_fleet import (
    FleetConfig,
    Partition,
    compute_partition_timeout_min,
    launch_partition_with_fallback,
    poll_for_all_partitions,
    terminate_all,
)
from contextpulse_pipeline.raw_source import RawSourceCollection

logger = logging.getLogger("voice-isolation-submit")

DEFAULT_BUCKET = "jerard-activefounder"
DEFAULT_AMI = "ami-012ba162b9cd2729c"  # DLAMI Ubuntu 22.04 PyTorch 2.7
DEFAULT_IAM_PROFILE = "contextpulse-transcription-worker-profile"
DEFAULT_SECURITY_GROUP = "sg-012ca22d2bed529d4"

# WeSep is GPU-friendly on L4; same fallback chain as phase1_5
DEFAULT_INSTANCE_TYPES = ["g6.xlarge", "g5.xlarge", "g6.2xlarge", "g4dn.xlarge"]

BOOT_SCRIPT_S3_KEY = "code/infra/boot/boot_voice_isolation.sh"
CODE_S3_PREFIX = "code/contextpulse_pipeline/"


def _user_data(spec_s3_uri: str, boot_script_s3_uri: str) -> str:
    return f"""#!/bin/bash
set -e
exec > /var/log/user-data.log 2>&1
echo "=== user-data starting $(date -u) ==="

cat > /etc/cpp-voice-isolation.env <<EOF
VOICE_ISOLATION_SPEC_S3_URI={spec_s3_uri}
EOF
chmod 644 /etc/cpp-voice-isolation.env

aws s3 cp {boot_script_s3_uri} /tmp/boot.sh --region us-east-1
chmod +x /tmp/boot.sh
exec /tmp/boot.sh
"""


def upload_pipeline_code(*, bucket: str, s3_client) -> None:
    pipeline_root = Path(__file__).resolve().parents[2]
    repo_root = pipeline_root.parent
    logger.info("Syncing %s -> s3://%s/%s", pipeline_root, bucket, CODE_S3_PREFIX)
    for py_file in pipeline_root.rglob("*.py"):
        if "__pycache__" in py_file.parts or "/tests/" in py_file.as_posix():
            continue
        rel = py_file.relative_to(pipeline_root)
        key = CODE_S3_PREFIX + rel.as_posix()
        s3_client.upload_file(str(py_file), bucket, key)
    boot_script = repo_root / "infra/boot/boot_voice_isolation.sh"
    if boot_script.exists():
        s3_client.upload_file(str(boot_script), bucket, BOOT_SCRIPT_S3_KEY)
        logger.info(
            "Uploaded boot_voice_isolation.sh -> s3://%s/%s", bucket, BOOT_SCRIPT_S3_KEY
        )
    else:
        raise FileNotFoundError(f"Boot script not found: {boot_script}")


def upload_inputs(
    *,
    raw_sources: Path,
    fingerprint_result: Path,
    bucket: str,
    container: str,
    s3_client,
    coll: RawSourceCollection,
) -> tuple[str, str, dict[str, str]]:
    rs_key = f"voice-isolation-input/{container}/raw_sources.json"
    s3_client.upload_file(str(raw_sources), bucket, rs_key)
    fp_key = f"voice-isolation-input/{container}/fingerprint_result.json"
    s3_client.upload_file(str(fingerprint_result), bucket, fp_key)
    logger.info("Uploaded fingerprint_result -> s3://%s/%s", bucket, fp_key)

    audio_prefix = f"voice-isolation-input/{container}/audio"
    basename_to_key: dict[str, str] = {}
    for rs in coll.sources:
        local_path = Path(rs.file_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Source missing on disk: {local_path}")
        key = f"{audio_prefix}/{local_path.name}"
        try:
            head = s3_client.head_object(Bucket=bucket, Key=key)
            if head["ContentLength"] == local_path.stat().st_size:
                basename_to_key[local_path.name] = key
                logger.info("Skipping %s — already in S3", local_path.name)
                continue
        except Exception:
            pass
        size_mb = local_path.stat().st_size / 1e6
        logger.info("Uploading %s (%.1f MB) -> s3://%s/%s", local_path.name, size_mb, bucket, key)
        s3_client.upload_file(str(local_path), bucket, key)
        basename_to_key[local_path.name] = key
    return rs_key, fp_key, basename_to_key


def upload_spec(
    *,
    container: str,
    raw_sources_key: str,
    fingerprint_key: str,
    basename_to_key: dict[str, str],
    bucket: str,
    output_prefix: str,
    model_source: str,
    top_k_sources_per_speaker: int | None,
    s3_client,
) -> str:
    spec = {
        "container": container,
        "s3_bucket": bucket,
        "raw_sources_s3_key": raw_sources_key,
        "fingerprint_result_s3_key": fingerprint_key,
        "audio_s3_keys": list(basename_to_key.values()),
        "output_prefix": output_prefix,
        "model_source": model_source,
        "top_k_sources_per_speaker": top_k_sources_per_speaker,
    }
    spec_key = f"voice-isolation-input/{container}/spec.json"
    s3_client.put_object(
        Bucket=bucket, Key=spec_key, Body=json.dumps(spec, indent=2).encode("utf-8")
    )
    return f"s3://{bucket}/{spec_key}"


def download_outputs(*, bucket: str, output_prefix: str, output_dir: Path, s3_client) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    iso_dir = output_dir / "voice_isolation"
    iso_dir.mkdir(parents=True, exist_ok=True)
    paginator = s3_client.get_paginator("list_objects_v2")
    n = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=output_prefix.rstrip("/")):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/_DONE") or key.endswith("/_FAILED"):
                continue
            name = Path(key).name
            if "/voice_isolation/" in key:
                local = iso_dir / name
            else:
                local = output_dir / name
            try:
                s3_client.download_file(bucket, key, str(local))
                n += 1
            except Exception as exc:
                logger.warning("Could not download %s: %s", key, exc)
    return n


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-sources", required=True)
    parser.add_argument("--fingerprint-result", required=True, help="Path to fingerprint_result.json (Phase 1.5 output)")
    parser.add_argument("--container", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--instance-types", default=",".join(DEFAULT_INSTANCE_TYPES))
    parser.add_argument(
        "--model-source",
        default="Wespeaker/wespeaker-voxceleb-resnet34",
        help="WeSep model source (HuggingFace repo)",
    )
    parser.add_argument(
        "--top-k-sources-per-speaker",
        type=int,
        default=None,
        help="Only extract on the K sources with the most chunks per speaker (cuts GPU time)",
    )
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name="us-east-1")
    ec2 = boto3.client("ec2", region_name="us-east-1")

    coll = RawSourceCollection.from_json(path=Path(args.raw_sources))
    logger.info(
        "Loaded %d sources (%.1f hr) from %s",
        len(coll.sources),
        sum(s.duration_sec for s in coll.sources) / 3600,
        args.raw_sources,
    )

    instance_types = [t.strip() for t in args.instance_types.split(",") if t.strip()]
    fleet_config = FleetConfig(
        instance_types=instance_types,
        ami=DEFAULT_AMI,
        iam_profile=DEFAULT_IAM_PROFILE,
        security_group=DEFAULT_SECURITY_GROUP,
        region="us-east-1",
        extra_tags={
            "Project": "ContextPulse",
            "Pipeline": "voice_isolation",
            "Container": args.container,
            "Name": f"cpp-voice-iso-{args.container}",
        },
    )

    upload_pipeline_code(bucket=args.bucket, s3_client=s3)
    rs_key, fp_key, basename_to_key = upload_inputs(
        raw_sources=Path(args.raw_sources),
        fingerprint_result=Path(args.fingerprint_result),
        bucket=args.bucket,
        container=args.container,
        s3_client=s3,
        coll=coll,
    )

    output_prefix = f"voice-isolation-output/{args.container}"
    spec_uri = upload_spec(
        container=args.container,
        raw_sources_key=rs_key,
        fingerprint_key=fp_key,
        basename_to_key=basename_to_key,
        bucket=args.bucket,
        output_prefix=output_prefix,
        model_source=args.model_source,
        top_k_sources_per_speaker=args.top_k_sources_per_speaker,
        s3_client=s3,
    )

    if args.no_launch:
        logger.info("--no-launch: inputs ready in S3 at %s", spec_uri)
        return 0

    partition = Partition(id="p0", sources=list(coll.sources))
    boot_script_s3_uri = f"s3://{args.bucket}/{BOOT_SCRIPT_S3_KEY}"
    user_data = _user_data(spec_uri, boot_script_s3_uri)

    instance_ids: list[str] = []
    statuses: dict[str, str] = {}
    try:
        result = launch_partition_with_fallback(
            partition, user_data=user_data, config=fleet_config, ec2_client=ec2
        )
        if result.instance_id is None:
            logger.error("Failed to launch any instance: %s", result.error)
            return 1
        instance_ids.append(result.instance_id)
        logger.info("Launched %s as %s", result.instance_id, result.instance_type)

        # WeSep RTF on L4 is ~0.4 — between phase1_5's 0.10 and phase1_transcribe's 0.30
        timeout_min = compute_partition_timeout_min(partition, rtf_estimate=0.40, floor_min=45)
        logger.info("Polling for completion (timeout=%d min)", timeout_min)
        statuses = poll_for_all_partitions(
            bucket=args.bucket,
            output_prefix_for=lambda pid: output_prefix,
            partition_ids=["p0"],
            timeout_min_for=lambda pid: timeout_min,
            s3_client=s3,
        )
        logger.info("Final status: %s", statuses)
    finally:
        if instance_ids:
            terminate_all(instance_ids, ec2_client=ec2)

    download_outputs(
        bucket=args.bucket,
        output_prefix=output_prefix,
        output_dir=Path(args.output_dir),
        s3_client=s3,
    )
    return 0 if statuses.get("p0") == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
