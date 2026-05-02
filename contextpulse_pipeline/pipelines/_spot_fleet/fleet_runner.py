# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Spot-fleet job runner — workload-agnostic.

Provides the partition + diversified-launch + per-partition-poll + terminate-all
machinery shared between phase1_transcribe (GPU) and future variants like
phase1_transcribe_cpu (Graviton). The caller supplies a FleetConfig (instance
type fallback chain, AMI, IAM, SG, boot script, user-data builder) and a list
of work units; this module handles the AWS orchestration.
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

from botocore.exceptions import ClientError

from contextpulse_pipeline.raw_source import RawSource

logger = logging.getLogger("spot-fleet")

# Errors that indicate "try a different instance type" rather than a real failure.
_RETRYABLE_LAUNCH_ERRORS = {
    "InsufficientInstanceCapacity",
    "SpotMaxPriceTooLow",
    "Unsupported",
    "InvalidParameterValue",
}


@dataclass
class Partition:
    """One unit of work assigned to one spot instance.

    `id` is a short stable label like "p0", "p1" used in S3 paths and logs.
    `sources` is the subset of RawSources this partition handles; the caller
    converts these into spec.json content (audio_s3_keys etc.) appropriate
    for its workload.
    """

    id: str
    sources: list[RawSource]

    @property
    def total_duration_sec(self) -> float:
        return sum(s.duration_sec for s in self.sources)


@dataclass
class FleetConfig:
    """Instance-type fallback chain and AMI/network/IAM context.

    The fallback chain is tried in order until one launch succeeds. Capacity
    failures (InsufficientInstanceCapacity, etc.) trigger the next type;
    non-capacity errors propagate.
    """

    instance_types: Sequence[str]  # priority order, e.g. ["g6.xlarge", "g5.xlarge"]
    ami: str
    iam_profile: str
    security_group: str
    region: str = "us-east-1"
    volume_size_gb: int = 100
    extra_tags: dict[str, str] = field(default_factory=dict)


@dataclass
class LaunchResult:
    """Result of attempting to launch one partition's spot instance."""

    partition_id: str
    instance_id: str | None  # None if all types in fallback chain failed
    instance_type: str | None  # The type that succeeded
    error: str | None = None  # Final error message if instance_id is None


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


def partition_sources(sources: Sequence[RawSource], n_partitions: int) -> list[Partition]:
    """Greedy bin-pack sources into N balanced partitions by total duration.

    Uses Longest-Processing-Time-first (LPT): sort sources descending by
    duration, place each into the currently-shortest partition. Provides
    near-optimal balance for our typical input sizes (5-20 sources per run).
    """
    if n_partitions < 1:
        raise ValueError(f"n_partitions must be >= 1, got {n_partitions}")
    if not sources:
        return []
    n = min(n_partitions, len(sources))  # never empty partitions

    # Sort by duration descending
    sorted_sources = sorted(sources, key=lambda s: s.duration_sec, reverse=True)

    partitions: list[Partition] = [Partition(id=f"p{i}", sources=[]) for i in range(n)]

    for src in sorted_sources:
        # Place into the partition with smallest current total
        target = min(partitions, key=lambda p: p.total_duration_sec)
        target.sources.append(src)

    return partitions


# ---------------------------------------------------------------------------
# Timeout calculation
# ---------------------------------------------------------------------------


def compute_partition_timeout_min(
    partition: Partition,
    *,
    rtf_estimate: float = 0.30,  # generous: actual L4 RTF is ~0.16
    boot_min: int = 10,
    safety_min: int = 15,
    floor_min: int = 30,
) -> int:
    """Compute a per-partition orchestrator polling timeout.

    Defaults are sized for transcription on L4 GPU. Caller can override
    rtf_estimate for CPU/different GPU pipelines.

    timeout = max(floor, boot + audio_min * rtf + safety)

    For a 2.3 hr partition on L4: 10 + 138*0.30 + 15 = 66 min.
    """
    audio_min = partition.total_duration_sec / 60.0
    computed = boot_min + int(audio_min * rtf_estimate) + safety_min
    return max(floor_min, computed)


# ---------------------------------------------------------------------------
# Launch with fallback
# ---------------------------------------------------------------------------


def launch_partition_with_fallback(
    partition: Partition,
    *,
    user_data: str,
    config: FleetConfig,
    ec2_client,
) -> LaunchResult:
    """Launch one spot instance, trying each type in fallback chain.

    Returns LaunchResult with instance_id set on success, or with error set
    if all types failed. Capacity failures advance the chain; non-capacity
    failures abort with that error captured.
    """
    user_data_b64 = base64.b64encode(user_data.encode("utf-8")).decode("ascii")

    last_error: str | None = None
    for itype in config.instance_types:
        logger.info("[%s] Attempting launch as %s", partition.id, itype)
        try:
            response = ec2_client.run_instances(
                ImageId=config.ami,
                InstanceType=itype,
                MinCount=1,
                MaxCount=1,
                IamInstanceProfile={"Name": config.iam_profile},
                SecurityGroupIds=[config.security_group],
                UserData=user_data_b64,
                InstanceMarketOptions={
                    "MarketType": "spot",
                    "SpotOptions": {
                        "SpotInstanceType": "one-time",
                        "InstanceInterruptionBehavior": "terminate",
                    },
                },
                InstanceInitiatedShutdownBehavior="terminate",
                BlockDeviceMappings=[
                    {
                        "DeviceName": "/dev/sda1",
                        "Ebs": {
                            "VolumeSize": config.volume_size_gb,
                            "VolumeType": "gp3",
                            "DeleteOnTermination": True,
                        },
                    }
                ],
                TagSpecifications=[
                    {
                        "ResourceType": "instance",
                        "Tags": [
                            {"Key": "Partition", "Value": partition.id},
                            *[
                                {"Key": k, "Value": v}
                                for k, v in config.extra_tags.items()
                            ],
                        ],
                    }
                ],
            )
            iid = response["Instances"][0]["InstanceId"]
            logger.info(
                "[%s] Launched %s as %s (audio=%.1f min)",
                partition.id,
                iid,
                itype,
                partition.total_duration_sec / 60.0,
            )
            return LaunchResult(partition_id=partition.id, instance_id=iid, instance_type=itype)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            msg = exc.response.get("Error", {}).get("Message", str(exc))
            last_error = f"{code}: {msg}"
            if code in _RETRYABLE_LAUNCH_ERRORS:
                logger.warning("[%s] %s -> trying next type", partition.id, code)
                continue
            logger.error("[%s] Non-retryable launch error %s: %s", partition.id, code, msg)
            return LaunchResult(
                partition_id=partition.id, instance_id=None, instance_type=None, error=last_error
            )

    logger.error("[%s] All instance types exhausted; last error: %s", partition.id, last_error)
    return LaunchResult(
        partition_id=partition.id, instance_id=None, instance_type=None, error=last_error
    )


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


def poll_for_all_partitions(
    *,
    bucket: str,
    output_prefix_for: Callable[[str], str],  # partition_id -> output_prefix
    partition_ids: Sequence[str],
    timeout_min_for: Callable[[str], int],  # partition_id -> per-partition timeout
    s3_client,
    poll_interval_sec: int = 30,
) -> dict[str, str]:
    """Poll for _DONE / _FAILED markers across N partitions.

    Returns dict[partition_id -> "done" | "failed" | "timeout"].

    Each partition has its own per-partition timeout. The whole call returns
    when every partition has reached a terminal state. Status is logged on
    every transition.
    """
    statuses: dict[str, str] = {}
    deadlines: dict[str, float] = {pid: time.time() + timeout_min_for(pid) * 60 for pid in partition_ids}

    while len(statuses) < len(partition_ids):
        for pid in partition_ids:
            if pid in statuses:
                continue
            prefix = output_prefix_for(pid).rstrip("/")
            done_key = f"{prefix}/_DONE"
            failed_key = f"{prefix}/_FAILED"
            try:
                s3_client.head_object(Bucket=bucket, Key=done_key)
                statuses[pid] = "done"
                logger.info("[%s] _DONE marker found at s3://%s/%s", pid, bucket, done_key)
                continue
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "NotFound"}:
                    pass  # other errors -- swallow and continue polling
            try:
                s3_client.head_object(Bucket=bucket, Key=failed_key)
                statuses[pid] = "failed"
                logger.warning("[%s] _FAILED marker found at s3://%s/%s", pid, bucket, failed_key)
                continue
            except ClientError:
                pass
            if time.time() > deadlines[pid]:
                statuses[pid] = "timeout"
                logger.error(
                    "[%s] timeout after %d min — no _DONE or _FAILED marker",
                    pid,
                    timeout_min_for(pid),
                )

        if len(statuses) < len(partition_ids):
            remaining = sorted(set(partition_ids) - set(statuses))
            logger.info("Polling... %d partitions outstanding: %s", len(remaining), remaining)
            time.sleep(poll_interval_sec)

    return statuses


# ---------------------------------------------------------------------------
# Termination (orchestrator owns lifecycle, per skill rule)
# ---------------------------------------------------------------------------


def terminate_all(instance_ids: Sequence[str], *, ec2_client) -> None:
    """Best-effort terminate every launched instance. Anti-pattern #15 from
    building-transcription-pipelines: orchestrator owns spot lifecycle, not
    the worker. Always called in a finally block from the caller.
    """
    valid_ids = [iid for iid in instance_ids if iid]
    if not valid_ids:
        return
    try:
        ec2_client.terminate_instances(InstanceIds=valid_ids)
        logger.info("Sent terminate-instances for %d instances: %s", len(valid_ids), valid_ids)
    except ClientError as exc:
        logger.warning("terminate-instances failed: %s -- retrying individually", exc)
        for iid in valid_ids:
            try:
                ec2_client.terminate_instances(InstanceIds=[iid])
                logger.info("Terminated %s individually", iid)
            except Exception as exc2:
                logger.error("Failed to terminate %s: %s", iid, exc2)
