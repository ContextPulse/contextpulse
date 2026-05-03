# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Phase 1 Transcribe-Only — redrive for missing partitions.

Use case: spot quota / capacity / interruption left some partitions un-launched
(or partially complete) on a previous run. This script reads the per-partition
specs already in S3, checks which ones don't have a `_DONE` marker, and
launches spot instances for ONLY the missing partitions.

The worker is idempotent at the file level (skip-if-exists on transcript JSON
in S3), so re-running a partially-complete partition is safe and cheap.

Usage:
    python -m contextpulse_pipeline.pipelines.phase1_transcribe.redrive \
        --container ep-2026-04-26-josh-cashman \
        --output-dir working/ep-2026-04-26-josh-cashman/transcripts
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from contextpulse_pipeline.pipelines._spot_fleet import (
    FleetConfig,
    LaunchResult,
    Partition,
    compute_partition_timeout_min,
    launch_partition_with_fallback,
    poll_for_all_partitions,
    terminate_all,
)
from contextpulse_pipeline.pipelines.phase1_transcribe.submit import (
    BOOT_SCRIPT_S3_KEY,
    DEFAULT_AMI,
    DEFAULT_BUCKET,
    DEFAULT_IAM_PROFILE,
    DEFAULT_INSTANCE_TYPES,
    DEFAULT_SECURITY_GROUP,
    _user_data,
    download_partition_outputs,
)
from contextpulse_pipeline.raw_source import RawSource, RawSourceCollection

logger = logging.getLogger("phase1-redrive")


def _list_partition_specs(*, bucket: str, container: str, s3_client) -> list[str]:
    """Return list of partition_ids that have specs in S3 (e.g., ['p0','p1','p2','p3'])."""
    prefix = f"phase1-input/{container}/spec_"
    pids: list[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # spec_p0.json -> p0
            stem = Path(key).stem  # spec_p0
            if stem.startswith("spec_"):
                pids.append(stem.removeprefix("spec_"))
    return sorted(pids)


def _partition_done(*, bucket: str, container: str, partition_id: str, s3_client) -> bool:
    """Check if this partition's _DONE marker exists in S3."""
    key = f"phase1-output/{container}/transcripts/{partition_id}/_DONE"
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def _load_partition_from_spec(*, bucket: str, container: str, partition_id: str, s3_client) -> Partition:
    """Reconstruct a Partition object from the spec + raw_sources subset already in S3."""
    spec_key = f"phase1-input/{container}/spec_{partition_id}.json"
    spec = json.loads(s3_client.get_object(Bucket=bucket, Key=spec_key)["Body"].read())
    rs_key = spec["raw_sources_s3_key"]
    rs_body = s3_client.get_object(Bucket=bucket, Key=rs_key)["Body"].read().decode("utf-8")
    coll = RawSourceCollection.from_json(rs_body)
    return Partition(id=partition_id, sources=list(coll.sources))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--output-dir", required=True, help="Local dir for downloaded transcripts")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument(
        "--instance-types",
        default=",".join(DEFAULT_INSTANCE_TYPES),
        help="Comma-separated fallback chain",
    )
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name="us-east-1")
    ec2 = boto3.client("ec2", region_name="us-east-1")

    all_pids = _list_partition_specs(bucket=args.bucket, container=args.container, s3_client=s3)
    logger.info("Found %d partition specs in S3: %s", len(all_pids), all_pids)
    if not all_pids:
        logger.error("No partition specs found at s3://%s/phase1-input/%s/spec_*.json", args.bucket, args.container)
        return 1

    # Identify which need redrive
    missing_pids: list[str] = []
    for pid in all_pids:
        if _partition_done(bucket=args.bucket, container=args.container, partition_id=pid, s3_client=s3):
            logger.info("[%s] _DONE marker present, skipping", pid)
        else:
            missing_pids.append(pid)
    logger.info("Partitions needing redrive: %s", missing_pids)
    if not missing_pids:
        logger.info("Nothing to do.")
        return 0

    # Fleet config (mirrors submit.py main)
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
            "Name": f"cpp-phase1-{args.container}-redrive",
            "Redrive": "true",
        },
    )

    # Reconstruct Partition objects
    partitions = [
        _load_partition_from_spec(
            bucket=args.bucket, container=args.container, partition_id=pid, s3_client=s3
        )
        for pid in missing_pids
    ]
    for p in partitions:
        logger.info(
            "  [%s] %d files, %.1f min audio, timeout=%d min",
            p.id,
            len(p.sources),
            p.total_duration_sec / 60,
            compute_partition_timeout_min(p),
        )

    # Launch each missing partition with fallback
    boot_script_s3_uri = f"s3://{args.bucket}/{BOOT_SCRIPT_S3_KEY}"
    launch_results: dict[str, LaunchResult] = {}
    instance_ids: list[str] = []

    try:
        for p in partitions:
            spec_uri = f"s3://{args.bucket}/phase1-input/{args.container}/spec_{p.id}.json"
            user_data = _user_data(spec_uri, boot_script_s3_uri)
            result = launch_partition_with_fallback(
                p, user_data=user_data, config=fleet_config, ec2_client=ec2
            )
            launch_results[p.id] = result
            if result.instance_id:
                instance_ids.append(result.instance_id)

        successful = [r for r in launch_results.values() if r.instance_id]
        if not successful:
            logger.error("All redrive launches failed. Aborting.")
            return 1
        logger.info(
            "Redrive launched %d/%d partitions: %s",
            len(successful),
            len(partitions),
            {r.partition_id: f"{r.instance_id}({r.instance_type})" for r in successful},
        )

        timeout_per_pid = {
            p.id: compute_partition_timeout_min(p)
            for p in partitions
            if launch_results[p.id].instance_id
        }
        for pid, t in timeout_per_pid.items():
            logger.info("  [%s] timeout = %d min", pid, t)

        statuses = poll_for_all_partitions(
            bucket=args.bucket,
            output_prefix_for=lambda pid: f"phase1-output/{args.container}/transcripts/{pid}",
            partition_ids=list(timeout_per_pid.keys()),
            timeout_min_for=lambda pid: timeout_per_pid[pid],
            s3_client=s3,
        )

        logger.info("Final redrive statuses: %s", statuses)
        any_success = any(v == "done" for v in statuses.values())

    finally:
        if instance_ids:
            terminate_all(instance_ids, ec2_client=ec2)

    successful_prefixes = [
        f"phase1-output/{args.container}/transcripts/{pid}"
        for pid, st in statuses.items()
        if st == "done"
    ]
    if successful_prefixes:
        download_partition_outputs(
            bucket=args.bucket,
            output_prefixes=successful_prefixes,
            output_dir=Path(args.output_dir),
            s3_client=s3,
        )

    return 0 if any_success else 1


if __name__ == "__main__":
    sys.exit(main())
