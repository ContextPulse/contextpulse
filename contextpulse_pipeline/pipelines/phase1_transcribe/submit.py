# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Phase 1 Transcribe-Only — local submission orchestrator.

Workflow (with fan-out):
    1. Partition raw_sources into N balanced subsets by audio duration
    2. Upload audio + pipeline code + boot script to S3 (once for all partitions)
    3. For each partition: upload a partition-scoped spec.json + raw_sources.json,
       launch one g6.xlarge spot instance (with fallback to g5/g6.2x/g4dn on
       capacity errors)
    4. Poll all partitions in parallel for _DONE / _FAILED markers (each with
       per-partition timeout sized to that partition's audio duration)
    5. Always terminate every launched instance at exit (orchestrator owns
       spot lifecycle — anti-pattern #15 from building-transcription-pipelines)
    6. Download all transcripts from per-partition output prefixes
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
    LaunchResult,
    compute_partition_timeout_min,
    launch_partition_with_fallback,
    partition_sources,
    poll_for_all_partitions,
    terminate_all,
)
from contextpulse_pipeline.raw_source import RawSourceCollection

logger = logging.getLogger("phase1-submit")

# AWS resources (discovered 2026-05-02)
DEFAULT_BUCKET = "jerard-activefounder"
DEFAULT_AMI = "ami-012ba162b9cd2729c"  # DLAMI Ubuntu 22.04 PyTorch 2.7
DEFAULT_IAM_PROFILE = "contextpulse-transcription-worker-profile"
DEFAULT_SECURITY_GROUP = "sg-012ca22d2bed529d4"  # contextpulse-transcription-worker-sg

# Capacity-diversified GPU instance fallback chain (priority order).
# DLAMI ami-012ba162b9cd2729c supports L4 (g6), A10G (g5), and T4 (g4dn).
# Spot placement score for g6.xlarge alone = 3/10; with this diversification = 9/10.
DEFAULT_INSTANCE_TYPES = ["g6.xlarge", "g5.xlarge", "g6.2xlarge", "g4dn.xlarge"]
DEFAULT_N_INSTANCES = 4

BOOT_SCRIPT_S3_KEY = "code/infra/boot/boot_phase1_transcribe.sh"
CODE_S3_PREFIX = "code/contextpulse_pipeline/"


def _user_data(spec_s3_uri: str, boot_script_s3_uri: str) -> str:
    """User-data for the spot instance: write spec env file, fetch + run boot script."""
    return f"""#!/bin/bash
set -e
exec > /var/log/user-data.log 2>&1
echo "=== user-data starting $(date -u) ==="

cat > /etc/cpp-phase1.env <<EOF
PHASE1_SPEC_S3_URI={spec_s3_uri}
EOF
chmod 644 /etc/cpp-phase1.env

aws s3 cp {boot_script_s3_uri} /tmp/boot.sh --region us-east-1
chmod +x /tmp/boot.sh
exec /tmp/boot.sh
"""


def upload_pipeline_code(*, bucket: str, s3_client) -> None:
    """Sync local contextpulse_pipeline/ to s3://<bucket>/code/contextpulse_pipeline/.

    Mirrors what `aws s3 sync` does but limited to .py files.
    """
    pipeline_root = Path(__file__).resolve().parents[2]  # contextpulse_pipeline/
    repo_root = pipeline_root.parent  # ContextPulse/
    logger.info("Syncing %s -> s3://%s/%s", pipeline_root, bucket, CODE_S3_PREFIX)
    for py_file in pipeline_root.rglob("*.py"):
        if "__pycache__" in py_file.parts or "/tests/" in py_file.as_posix():
            continue
        rel = py_file.relative_to(pipeline_root)
        key = CODE_S3_PREFIX + rel.as_posix()
        s3_client.upload_file(str(py_file), bucket, key)
    # Boot script too
    boot_script = repo_root / "infra/boot/boot_phase1_transcribe.sh"
    if boot_script.exists():
        s3_client.upload_file(str(boot_script), bucket, BOOT_SCRIPT_S3_KEY)
        logger.info("Uploaded boot_phase1_transcribe.sh -> s3://%s/%s", bucket, BOOT_SCRIPT_S3_KEY)


def upload_audio_files(coll: RawSourceCollection, *, bucket: str, container: str, s3_client) -> dict[str, str]:
    """Upload all audio files to s3://<bucket>/phase1-input/<container>/audio/.

    Returns dict[basename -> s3_key] for spec generation.
    """
    audio_prefix = f"phase1-input/{container}/audio"
    basename_to_key: dict[str, str] = {}
    for rs in coll.sources:
        local_path = Path(rs.file_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Source missing on disk: {local_path}")
        key = f"{audio_prefix}/{local_path.name}"
        size_mb = local_path.stat().st_size / 1e6
        logger.info("Uploading %s (%.1f MB) -> s3://%s/%s", local_path.name, size_mb, bucket, key)
        s3_client.upload_file(str(local_path), bucket, key)
        basename_to_key[local_path.name] = key
    return basename_to_key


def upload_partition_spec(
    *,
    partition_id: str,
    sources_subset,
    basename_to_key: dict[str, str],
    bucket: str,
    container: str,
    model: str,
    s3_client,
) -> tuple[str, str]:
    """Upload a partition-scoped spec.json + raw_sources.json subset to S3.

    Returns (spec_s3_uri, output_prefix).
    """
    output_prefix = f"phase1-output/{container}/transcripts/{partition_id}"

    # Partition's raw_sources.json — only the subset for this partition
    rs_subset = RawSourceCollection(container=container, sources=list(sources_subset))
    rs_key = f"phase1-input/{container}/raw_sources_{partition_id}.json"
    s3_client.put_object(Bucket=bucket, Key=rs_key, Body=rs_subset.to_json().encode("utf-8"))
    logger.info("[%s] Uploaded raw_sources subset (%d sources)", partition_id, len(rs_subset.sources))

    # Spec for this partition
    audio_keys = [basename_to_key[Path(rs.file_path).name] for rs in sources_subset]
    spec = {
        "container": container,
        "partition_id": partition_id,
        "model": model,
        "s3_bucket": bucket,
        "audio_s3_keys": audio_keys,
        "output_prefix": output_prefix,
        "raw_sources_s3_key": rs_key,
    }
    spec_key = f"phase1-input/{container}/spec_{partition_id}.json"
    s3_client.put_object(
        Bucket=bucket, Key=spec_key, Body=json.dumps(spec, indent=2).encode("utf-8")
    )
    spec_uri = f"s3://{bucket}/{spec_key}"
    logger.info("[%s] Uploaded spec -> %s (%d files, %.1f hr audio)", partition_id, spec_uri, len(audio_keys), sum(rs.duration_sec for rs in sources_subset) / 3600)

    return spec_uri, output_prefix


def download_partition_outputs(
    *,
    bucket: str,
    output_prefixes: list[str],
    output_dir: Path,
    s3_client,
) -> int:
    """Download all transcript files from each partition's output prefix.

    All files land in the same local output_dir (per-source filenames are
    unique by sha16, so partitions don't collide).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paginator = s3_client.get_paginator("list_objects_v2")
    n = 0
    for prefix in output_prefixes:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix.rstrip("/")):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/_DONE") or key.endswith("/_FAILED"):
                    continue
                local_path = output_dir / Path(key).name
                s3_client.download_file(bucket, key, str(local_path))
                n += 1
    logger.info("Downloaded %d transcript files to %s", n, output_dir)
    return n


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-sources", required=True, help="Path to raw_sources.json")
    parser.add_argument("--container", required=True)
    parser.add_argument("--output-dir", required=True, help="Local dir for downloaded transcripts")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument(
        "--n-instances",
        type=int,
        default=DEFAULT_N_INSTANCES,
        help="Number of partitions / parallel spot instances (default 4)",
    )
    parser.add_argument(
        "--instance-types",
        default=",".join(DEFAULT_INSTANCE_TYPES),
        help="Comma-separated fallback chain (default: g6.xlarge,g5.xlarge,g6.2xlarge,g4dn.xlarge)",
    )
    parser.add_argument("--model", default="large-v3")
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Upload all inputs + partition specs only; skip spot launches (smoke mode)",
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
            "Pipeline": "phase1_transcribe",
            "Container": args.container,
            "Name": f"cpp-phase1-{args.container}",
        },
    )

    # Partition sources
    partitions = partition_sources(coll.sources, n_partitions=args.n_instances)
    logger.info(
        "Partitioned %d sources into %d partitions:", len(coll.sources), len(partitions)
    )
    for p in partitions:
        logger.info(
            "  [%s] %d files, %.1f min audio, ~%.1f min compute (RTF 0.20)",
            p.id,
            len(p.sources),
            p.total_duration_sec / 60,
            p.total_duration_sec / 60 * 0.20,
        )

    # Upload pipeline code + boot script (once for all partitions)
    upload_pipeline_code(bucket=args.bucket, s3_client=s3)

    # Upload all audio files (once for all partitions; specs reference subsets)
    basename_to_key = upload_audio_files(
        coll, bucket=args.bucket, container=args.container, s3_client=s3
    )

    # Upload per-partition specs
    partition_specs: dict[str, tuple[str, str]] = {}  # pid -> (spec_uri, output_prefix)
    for p in partitions:
        spec_uri, output_prefix = upload_partition_spec(
            partition_id=p.id,
            sources_subset=p.sources,
            basename_to_key=basename_to_key,
            bucket=args.bucket,
            container=args.container,
            model=args.model,
            s3_client=s3,
        )
        partition_specs[p.id] = (spec_uri, output_prefix)

    if args.no_launch:
        logger.info("--no-launch: %d partitions ready in S3, skipping spot launches.", len(partitions))
        for p in partitions:
            spec_uri, _ = partition_specs[p.id]
            logger.info("  [%s] Spec: %s", p.id, spec_uri)
        return 0

    # Launch each partition with fallback
    boot_script_s3_uri = f"s3://{args.bucket}/{BOOT_SCRIPT_S3_KEY}"
    launch_results: dict[str, LaunchResult] = {}
    instance_ids: list[str] = []

    try:
        for p in partitions:
            spec_uri, _ = partition_specs[p.id]
            user_data = _user_data(spec_uri, boot_script_s3_uri)
            result = launch_partition_with_fallback(
                p, user_data=user_data, config=fleet_config, ec2_client=ec2
            )
            launch_results[p.id] = result
            if result.instance_id:
                instance_ids.append(result.instance_id)

        successful_launches = [r for r in launch_results.values() if r.instance_id]
        if not successful_launches:
            logger.error("All partitions failed to launch. Aborting.")
            return 1
        logger.info(
            "Launched %d/%d partitions: %s",
            len(successful_launches),
            len(partitions),
            {r.partition_id: f"{r.instance_id}({r.instance_type})" for r in successful_launches},
        )

        # Per-partition timeouts (sized to each partition's audio duration)
        timeout_per_pid = {
            p.id: compute_partition_timeout_min(p) for p in partitions if launch_results[p.id].instance_id
        }
        for pid, t in timeout_per_pid.items():
            logger.info("  [%s] timeout = %d min", pid, t)

        # Output prefix lookup for polling
        def output_prefix_for(pid: str) -> str:
            return partition_specs[pid][1]

        statuses = poll_for_all_partitions(
            bucket=args.bucket,
            output_prefix_for=output_prefix_for,
            partition_ids=list(timeout_per_pid.keys()),
            timeout_min_for=lambda pid: timeout_per_pid[pid],
            s3_client=s3,
        )

        logger.info("Final partition statuses: %s", statuses)
        any_success = any(v == "done" for v in statuses.values())

    finally:
        if instance_ids:
            terminate_all(instance_ids, ec2_client=ec2)

    # Download whatever transcripts landed (even from failed partitions — partial outputs may exist)
    successful_prefixes = [partition_specs[pid][1] for pid, st in statuses.items() if st == "done"]
    failed_prefixes = [partition_specs[pid][1] for pid, st in statuses.items() if st != "done"]
    if successful_prefixes:
        download_partition_outputs(
            bucket=args.bucket,
            output_prefixes=successful_prefixes,
            output_dir=Path(args.output_dir),
            s3_client=s3,
        )
    # Also try to download partial outputs from failed partitions (worker may have
    # uploaded some files before crashing — incremental upload pattern)
    if failed_prefixes:
        logger.warning(
            "Attempting partial-output download from %d failed partitions", len(failed_prefixes)
        )
        download_partition_outputs(
            bucket=args.bucket,
            output_prefixes=failed_prefixes,
            output_dir=Path(args.output_dir),
            s3_client=s3,
        )

    return 0 if any_success else 1


if __name__ == "__main__":
    sys.exit(main())
