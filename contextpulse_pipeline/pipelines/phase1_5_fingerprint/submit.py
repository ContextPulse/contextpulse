# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Phase 1.5 ECAPA Fingerprinting — local submission orchestrator.

Single-instance variant of the spot pipeline pattern. Unlike phase1_transcribe
(N-way fan-out across partitions for embarrassingly-parallel transcription),
fingerprinting clusters across ALL sources at once — splitting it would
require a centroid-merging post-pass that's not worth the complexity for
typical episode sizes (3-20 sources).

Workflow:
    1. Upload pipeline code + boot script to S3 (same as phase1_transcribe)
    2. Upload unified_transcript.json + raw_sources.json + audio files
    3. Upload spec.json
    4. Launch one g6.xlarge spot instance with capacity-diversified fallback
    5. Poll for _DONE / _FAILED marker
    6. Download fingerprint_result.json + unified_transcript_labeled.json
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

logger = logging.getLogger("phase1-5-submit")

# Match phase1_transcribe defaults — same AWS resources are reused
DEFAULT_BUCKET = "jerard-activefounder"
DEFAULT_AMI = "ami-012ba162b9cd2729c"  # DLAMI Ubuntu 22.04 PyTorch 2.7
DEFAULT_IAM_PROFILE = "contextpulse-transcription-worker-profile"
DEFAULT_SECURITY_GROUP = "sg-012ca22d2bed529d4"

# Same fallback chain as phase1_transcribe; ECAPA fits comfortably on L4 / A10G / T4
DEFAULT_INSTANCE_TYPES = ["g6.xlarge", "g5.xlarge", "g6.2xlarge", "g4dn.xlarge"]

BOOT_SCRIPT_S3_KEY = "code/infra/boot/boot_phase1_5_fingerprint.sh"
CODE_S3_PREFIX = "code/contextpulse_pipeline/"


def _user_data(spec_s3_uri: str, boot_script_s3_uri: str) -> str:
    return f"""#!/bin/bash
set -e
exec > /var/log/user-data.log 2>&1
echo "=== user-data starting $(date -u) ==="

cat > /etc/cpp-phase1-5.env <<EOF
PHASE1_5_SPEC_S3_URI={spec_s3_uri}
EOF
chmod 644 /etc/cpp-phase1-5.env

aws s3 cp {boot_script_s3_uri} /tmp/boot.sh --region us-east-1
chmod +x /tmp/boot.sh
exec /tmp/boot.sh
"""


def upload_pipeline_code(*, bucket: str, s3_client) -> None:
    """Sync local contextpulse_pipeline/ to s3://<bucket>/code/contextpulse_pipeline/.

    Skips __pycache__ and tests/. Uploads the slim boot_phase1_5_fingerprint.sh
    alongside the existing boot scripts.
    """
    pipeline_root = Path(__file__).resolve().parents[2]
    repo_root = pipeline_root.parent
    logger.info("Syncing %s -> s3://%s/%s", pipeline_root, bucket, CODE_S3_PREFIX)
    for py_file in pipeline_root.rglob("*.py"):
        if "__pycache__" in py_file.parts or "/tests/" in py_file.as_posix():
            continue
        rel = py_file.relative_to(pipeline_root)
        key = CODE_S3_PREFIX + rel.as_posix()
        s3_client.upload_file(str(py_file), bucket, key)
    boot_script = repo_root / "infra/boot/boot_phase1_5_fingerprint.sh"
    if boot_script.exists():
        s3_client.upload_file(str(boot_script), bucket, BOOT_SCRIPT_S3_KEY)
        logger.info(
            "Uploaded boot_phase1_5_fingerprint.sh -> s3://%s/%s", bucket, BOOT_SCRIPT_S3_KEY
        )
    else:
        raise FileNotFoundError(f"Boot script not found: {boot_script}")


def upload_inputs(
    *,
    raw_sources: Path,
    unified_transcript: Path,
    bucket: str,
    container: str,
    s3_client,
    coll: RawSourceCollection,
) -> tuple[str, str, dict[str, str]]:
    """Upload raw_sources.json, unified_transcript.json, and all audio files.

    Returns (raw_sources_key, unified_transcript_key, basename_to_audio_key).
    """
    rs_key = f"phase1-5-input/{container}/raw_sources.json"
    s3_client.upload_file(str(raw_sources), bucket, rs_key)
    logger.info("Uploaded raw_sources -> s3://%s/%s", bucket, rs_key)

    ut_key = f"phase1-5-input/{container}/unified_transcript.json"
    s3_client.upload_file(str(unified_transcript), bucket, ut_key)
    logger.info("Uploaded unified_transcript -> s3://%s/%s", bucket, ut_key)

    audio_prefix = f"phase1-5-input/{container}/audio"
    basename_to_key: dict[str, str] = {}
    for rs in coll.sources:
        local_path = Path(rs.file_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Source missing on disk: {local_path}")
        key = f"{audio_prefix}/{local_path.name}"
        size_mb = local_path.stat().st_size / 1e6
        # Skip if already uploaded (head_object equality check via size)
        try:
            head = s3_client.head_object(Bucket=bucket, Key=key)
            if head["ContentLength"] == local_path.stat().st_size:
                logger.info("Skipping %s — already in S3 at correct size", local_path.name)
                basename_to_key[local_path.name] = key
                continue
        except Exception:
            pass
        logger.info("Uploading %s (%.1f MB) -> s3://%s/%s", local_path.name, size_mb, bucket, key)
        s3_client.upload_file(str(local_path), bucket, key)
        basename_to_key[local_path.name] = key
    return rs_key, ut_key, basename_to_key


def upload_spec(
    *,
    container: str,
    raw_sources_key: str,
    unified_transcript_key: str,
    basename_to_key: dict[str, str],
    bucket: str,
    output_prefix: str,
    distance_threshold: float,
    min_chunk_sec: float,
    target_chunk_sec: float,
    max_clusters: int | None,
    model_source: str,
    s3_client,
) -> str:
    """Upload spec.json. Returns spec S3 URI."""
    spec = {
        "container": container,
        "s3_bucket": bucket,
        "raw_sources_s3_key": raw_sources_key,
        "unified_transcript_s3_key": unified_transcript_key,
        "audio_s3_keys": list(basename_to_key.values()),
        "output_prefix": output_prefix,
        "distance_threshold": distance_threshold,
        "min_chunk_sec": min_chunk_sec,
        "target_chunk_sec": target_chunk_sec,
        "max_clusters": max_clusters,
        "model_source": model_source,
    }
    spec_key = f"phase1-5-input/{container}/spec.json"
    s3_client.put_object(
        Bucket=bucket, Key=spec_key, Body=json.dumps(spec, indent=2).encode("utf-8")
    )
    logger.info("Uploaded spec -> s3://%s/%s", bucket, spec_key)
    return f"s3://{bucket}/{spec_key}"


def download_outputs(
    *,
    bucket: str,
    output_prefix: str,
    output_dir: Path,
    s3_client,
) -> int:
    """Download fingerprint_result.json + unified_transcript_labeled.json."""
    output_dir.mkdir(parents=True, exist_ok=True)
    keys = [
        f"{output_prefix.rstrip('/')}/fingerprint_result.json",
        f"{output_prefix.rstrip('/')}/unified_transcript_labeled.json",
    ]
    n = 0
    for key in keys:
        local = output_dir / Path(key).name
        try:
            s3_client.download_file(bucket, key, str(local))
            n += 1
            logger.info("Downloaded %s", local)
        except Exception as exc:
            logger.warning("Could not download %s: %s", key, exc)
    return n


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-sources", required=True, help="Path to raw_sources.json")
    parser.add_argument(
        "--unified-transcript",
        required=True,
        help="Path to unified_transcript.json (from Phase 1.6)",
    )
    parser.add_argument("--container", required=True)
    parser.add_argument("--output-dir", required=True, help="Local dir for downloaded outputs")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument(
        "--instance-types",
        default=",".join(DEFAULT_INSTANCE_TYPES),
        help="Comma-separated fallback chain",
    )
    parser.add_argument("--distance-threshold", type=float, default=0.5)
    parser.add_argument("--min-chunk-sec", type=float, default=2.0)
    parser.add_argument("--target-chunk-sec", type=float, default=4.0)
    parser.add_argument(
        "--max-clusters",
        type=int,
        default=None,
        help="Soft cap on cluster count (merges smallest into nearest)",
    )
    parser.add_argument(
        "--model-source",
        default="speechbrain/spkrec-ecapa-voxceleb",
        help="speechbrain model source (HuggingFace repo)",
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Upload all inputs only; skip spot launch (smoke mode)",
    )
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name="us-east-1")
    ec2 = boto3.client("ec2", region_name="us-east-1")

    coll = RawSourceCollection.from_json(path=Path(args.raw_sources))
    logger.info(
        "Loaded %d sources (%.1f hr total audio) from %s",
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
            "Pipeline": "phase1_5_fingerprint",
            "Container": args.container,
            "Name": f"cpp-phase1-5-{args.container}",
        },
    )

    # Upload code + inputs
    upload_pipeline_code(bucket=args.bucket, s3_client=s3)
    rs_key, ut_key, basename_to_key = upload_inputs(
        raw_sources=Path(args.raw_sources),
        unified_transcript=Path(args.unified_transcript),
        bucket=args.bucket,
        container=args.container,
        s3_client=s3,
        coll=coll,
    )

    output_prefix = f"phase1-5-output/{args.container}"
    spec_uri = upload_spec(
        container=args.container,
        raw_sources_key=rs_key,
        unified_transcript_key=ut_key,
        basename_to_key=basename_to_key,
        bucket=args.bucket,
        output_prefix=output_prefix,
        distance_threshold=args.distance_threshold,
        min_chunk_sec=args.min_chunk_sec,
        target_chunk_sec=args.target_chunk_sec,
        max_clusters=args.max_clusters,
        model_source=args.model_source,
        s3_client=s3,
    )

    if args.no_launch:
        logger.info("--no-launch: inputs ready in S3 at %s, skipping spot launch", spec_uri)
        return 0

    # Single-instance launch (re-uses Partition abstraction with one element)
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

        # Fingerprinting RTF on L4 is ~0.05; use a lower estimate than transcribe (0.30).
        timeout_min = compute_partition_timeout_min(partition, rtf_estimate=0.10, floor_min=30)
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

    # Always attempt to download (partial outputs may exist on failure)
    download_outputs(
        bucket=args.bucket,
        output_prefix=output_prefix,
        output_dir=Path(args.output_dir),
        s3_client=s3,
    )
    return 0 if statuses.get("p0") == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
