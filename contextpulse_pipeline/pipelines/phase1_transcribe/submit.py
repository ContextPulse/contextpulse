# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Jerard Ventures LLC
"""Phase 1 Transcribe-Only — local submission orchestrator.

Workflow:
    1. Upload audio files for each RawSource to s3://<bucket>/phase1-input/<container>/audio/
    2. Upload raw_sources.json to s3
    3. Upload spec.json to s3
    4. Launch a g6.xlarge spot instance (or N for parallelism) with user-data
       that pulls boot_phase1_transcribe.sh from S3 and runs it
    5. Poll for s3://<bucket>/<output_prefix>/_DONE (or _FAILED)
    6. Download all transcripts to local working dir
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
import time
from pathlib import Path

import boto3

from contextpulse_pipeline.raw_source import RawSourceCollection

logger = logging.getLogger("phase1-submit")

# AWS resources (discovered 2026-05-02)
DEFAULT_BUCKET = "jerard-activefounder"
DEFAULT_AMI = "ami-012ba162b9cd2729c"  # DLAMI Ubuntu 22.04 PyTorch 2.7
DEFAULT_INSTANCE_TYPE = "g6.xlarge"
DEFAULT_IAM_PROFILE = "contextpulse-transcription-worker-profile"
DEFAULT_SECURITY_GROUP = "sg-012ca22d2bed529d4"  # contextpulse-transcription-worker-sg
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


def upload_inputs(
    coll: RawSourceCollection,
    *,
    bucket: str,
    container: str,
    s3_client,
) -> tuple[str, str, list[str]]:
    """Upload all audio files + raw_sources.json + spec.json to S3.

    Returns (raw_sources_s3_key, spec_s3_uri, audio_s3_keys).
    """
    audio_prefix = f"phase1-input/{container}/audio"
    audio_keys: list[str] = []
    for rs in coll.sources:
        local_path = Path(rs.file_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Source missing on disk: {local_path}")
        key = f"{audio_prefix}/{local_path.name}"
        size_mb = local_path.stat().st_size / 1e6
        logger.info("Uploading %s (%.1f MB) -> s3://%s/%s", local_path.name, size_mb, bucket, key)
        s3_client.upload_file(str(local_path), bucket, key)
        audio_keys.append(key)

    rs_key = f"phase1-input/{container}/raw_sources.json"
    s3_client.put_object(
        Bucket=bucket,
        Key=rs_key,
        Body=coll.to_json().encode("utf-8"),
    )
    logger.info("Uploaded raw_sources.json")

    spec = {
        "container": container,
        "model": "large-v3",
        "s3_bucket": bucket,
        "audio_s3_keys": audio_keys,
        "output_prefix": f"phase1-output/{container}/transcripts",
        "raw_sources_s3_key": rs_key,
    }
    spec_key = f"phase1-input/{container}/spec.json"
    s3_client.put_object(
        Bucket=bucket, Key=spec_key, Body=json.dumps(spec, indent=2).encode("utf-8")
    )
    spec_uri = f"s3://{bucket}/{spec_key}"
    logger.info("Uploaded spec -> %s", spec_uri)

    return rs_key, spec_uri, audio_keys


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


def launch_spot(
    *,
    spec_s3_uri: str,
    bucket: str,
    container: str,
    instance_type: str,
    ami: str,
    iam_profile: str,
    security_group: str,
    ec2_client,
) -> str:
    """Launch one g6.xlarge spot instance. Returns instance ID."""
    boot_script_s3_uri = f"s3://{bucket}/{BOOT_SCRIPT_S3_KEY}"
    user_data = _user_data(spec_s3_uri, boot_script_s3_uri)
    user_data_b64 = base64.b64encode(user_data.encode("utf-8")).decode("ascii")

    response = ec2_client.run_instances(
        ImageId=ami,
        InstanceType=instance_type,
        MinCount=1,
        MaxCount=1,
        IamInstanceProfile={"Name": iam_profile},
        SecurityGroupIds=[security_group],
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
                "Ebs": {"VolumeSize": 100, "VolumeType": "gp3", "DeleteOnTermination": True},
            }
        ],
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": f"cpp-phase1-{container}"},
                    {"Key": "Project", "Value": "ContextPulse"},
                    {"Key": "Pipeline", "Value": "phase1_transcribe"},
                    {"Key": "Container", "Value": container},
                ],
            }
        ],
    )
    iid = response["Instances"][0]["InstanceId"]
    logger.info("Launched spot instance %s (%s) for container %s", iid, instance_type, container)
    return iid


def poll_for_done(*, bucket: str, container: str, s3_client, timeout_min: int = 60) -> bool:
    """Poll for _DONE or _FAILED marker. Returns True on _DONE, False on _FAILED."""
    output_prefix = f"phase1-output/{container}/transcripts"
    done_key = f"{output_prefix}/_DONE"
    failed_key = f"{output_prefix}/_FAILED"
    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        try:
            s3_client.head_object(Bucket=bucket, Key=done_key)
            logger.info("DONE marker found at s3://%s/%s", bucket, done_key)
            return True
        except Exception:
            pass
        try:
            s3_client.head_object(Bucket=bucket, Key=failed_key)
            obj = s3_client.get_object(Bucket=bucket, Key=failed_key)
            logger.error("FAILED marker found: %s", obj["Body"].read().decode("utf-8")[:500])
            return False
        except Exception:
            pass
        time.sleep(30)
        logger.info("Polling... (no marker yet)")
    logger.error("Polling timeout after %d min", timeout_min)
    return False


def download_outputs(
    *,
    bucket: str,
    container: str,
    output_dir: Path,
    s3_client,
) -> int:
    """Download all *.json + *.txt from output prefix to output_dir."""
    output_prefix = f"phase1-output/{container}/transcripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    paginator = s3_client.get_paginator("list_objects_v2")
    n = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=output_prefix):
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
    parser.add_argument("--instance-type", default=DEFAULT_INSTANCE_TYPE)
    parser.add_argument(
        "--no-launch", action="store_true", help="Upload inputs only, skip spot launch"
    )
    parser.add_argument("--no-poll", action="store_true", help="Launch but skip polling")
    args = parser.parse_args()

    s3 = boto3.client("s3", region_name="us-east-1")
    ec2 = boto3.client("ec2", region_name="us-east-1")

    coll = RawSourceCollection.from_json(path=Path(args.raw_sources))
    logger.info("Loaded %d sources from %s", len(coll.sources), args.raw_sources)

    upload_pipeline_code(bucket=args.bucket, s3_client=s3)
    rs_key, spec_uri, audio_keys = upload_inputs(
        coll, bucket=args.bucket, container=args.container, s3_client=s3
    )

    if args.no_launch:
        logger.info("Inputs uploaded. Skipping spot launch (--no-launch).")
        return 0

    iid = launch_spot(
        spec_s3_uri=spec_uri,
        bucket=args.bucket,
        container=args.container,
        instance_type=args.instance_type,
        ami=DEFAULT_AMI,
        iam_profile=DEFAULT_IAM_PROFILE,
        security_group=DEFAULT_SECURITY_GROUP,
        ec2_client=ec2,
    )

    if args.no_poll:
        logger.info("Launched %s. Skipping poll (--no-poll). Spec: %s", iid, spec_uri)
        return 0

    success = poll_for_done(bucket=args.bucket, container=args.container, s3_client=s3)
    if success:
        download_outputs(
            bucket=args.bucket,
            container=args.container,
            output_dir=Path(args.output_dir),
            s3_client=s3,
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
