# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Workload-agnostic spot-fleet job runner.

Reusable orchestration for "fan out N independent units of work across N spot
instances, with capacity-diversified instance-type fallback." Used by
phase1_transcribe (GPU inference) and intended to be used by future variants
(phase1_transcribe_cpu on Graviton, etc.) by swapping the FleetConfig.

See architecting-aws-solutions/PATTERNS.md (Pattern 2/5) for the canonical
description of this pattern.
"""
from contextpulse_pipeline.pipelines._spot_fleet.fleet_runner import (
    FleetConfig,
    LaunchResult,
    Partition,
    compute_partition_timeout_min,
    launch_partition_with_fallback,
    partition_sources,
    poll_for_all_partitions,
    terminate_all,
)

__all__ = [
    "FleetConfig",
    "LaunchResult",
    "Partition",
    "compute_partition_timeout_min",
    "launch_partition_with_fallback",
    "partition_sources",
    "poll_for_all_partitions",
    "terminate_all",
]
