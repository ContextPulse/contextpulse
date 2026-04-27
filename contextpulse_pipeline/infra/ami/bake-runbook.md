# ContextPulse Pipeline G6 AMI Bake Runbook

## When to bake a new AMI

- ML stack version change (faster-whisper, whisperx, pyannote version bump)
- Python version upgrade
- New system dependency added
- Every 3 months as a freshness cadence

## AMI lineage

| Version | AMI ID | Base AMI | Date | Notes |
|---------|--------|----------|------|-------|
| v1 | TBD (bake in progress 2026-04-26) | ami-00613b158c7a09b63 (DLAMI GPU PyTorch 2.7 Ubuntu 22.04) | 2026-04-26 | faster-whisper>=1.0.3, whisperx>=3.1.5, pyannote.audio==3.1.1, large-v3 pre-downloaded |

## Step-by-step: Bake v2 (when stack updates)

### 1. Find the latest base AMI

```bash
aws ec2 describe-images \
  --owners amazon \
  --filters "Name=name,Values=Deep Learning OSS Nvidia Driver AMI GPU PyTorch*Ubuntu*" \
  --query "sort_by(Images, &CreationDate)[-1].{ImageId:ImageId,Name:Name}" \
  --output table
```

### 2. Upload updated contextpulse_pipeline source

```bash
aws s3 sync ~/Projects/ContextPulse/contextpulse_pipeline/ \
    s3://jerard-activefounder/code/contextpulse_pipeline/ \
    --exclude "*.pyc" --exclude "__pycache__/*" --exclude ".venv/*" --exclude "tests/*"
```

### 3. Create the bake security group (if not already existing)

```bash
SG_ID=$(aws ec2 create-security-group \
    --group-name "contextpulse-ami-bake-sg" \
    --description "ContextPulse AMI bake - SSM outbound only" \
    --vpc-id vpc-0a8889c6ce0adbe5a \
    --query "GroupId" --output text)
```

If it already exists:
```bash
SG_ID=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=contextpulse-ami-bake-sg" \
    --query "SecurityGroups[0].GroupId" --output text)
```

### 4. Launch the bake instance

Use the user-data pattern from `bake-g6-ami.sh` that pulls the script from S3.
Key parameters:
- AMI: latest from step 1
- Instance type: g6.xlarge
- IAM profile: contextpulse-transcription-worker-profile
- Root volume: 200 GiB gp3 (models take ~80 GB)
- User-data: pulls bake-g6-ami.sh from S3 and runs it
- shutdown-behavior: stop (so you can snapshot, then terminate)

```bash
aws ec2 run-instances \
    --image-id <BASE_AMI_ID> \
    --instance-type g6.xlarge \
    --iam-instance-profile Name=contextpulse-transcription-worker-profile \
    --security-group-ids <SG_ID> \
    --subnet-id subnet-0043dab3b76ab067d \
    --instance-initiated-shutdown-behavior stop \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":200,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=cpp-ami-bake-temp},{Key=Project,Value=ContextPulsePipeline},{Key=Stage,Value=bake}]' \
    --metadata-options '{"HttpEndpoint":"enabled","HttpTokens":"required"}'
```

### 5. Monitor the bake

Via SSM Session Manager (no SSH key needed):
```bash
aws ssm start-session --target <INSTANCE_ID>
# Then on the instance:
tail -f /var/log/cpp-bake.log
```

Or stream the log via CloudWatch once the bake script redirects there.

Expected time: 30-50 min (model download is the long pole).

### 6. Snapshot the instance

Once the bake log shows "AMI bake complete!":
```bash
AMI_ID=$(aws ec2 create-image \
    --instance-id <INSTANCE_ID> \
    --name "contextpulse-pipeline-g6-v2" \
    --description "ContextPulse Pipeline GPU spot worker AMI v2 (faster-whisper + WhisperX + pyannote 3.1, models pre-downloaded)" \
    --no-reboot \
    --query "ImageId" --output text)

# Wait for AMI to be available (can take 10-20 min)
aws ec2 wait image-available --image-ids "$AMI_ID"
echo "AMI ready: $AMI_ID"
```

### 7. Update the launch template with the new AMI

```bash
aws ec2 create-launch-template-version \
    --launch-template-name contextpulse-pipeline-g6-launch-template-v1 \
    --source-version 1 \
    --version-description "v2 - <date>" \
    --launch-template-data "{\"ImageId\": \"$AMI_ID\"}"
```

Set as default:
```bash
aws ec2 modify-launch-template \
    --launch-template-name contextpulse-pipeline-g6-launch-template-v1 \
    --default-version <NEW_VERSION_NUMBER>
```

### 8. Terminate the bake instance

```bash
aws ec2 terminate-instances --instance-ids <INSTANCE_ID>
```

### 9. Update this runbook

Add a row to the AMI lineage table above.

## Notes

- Never use `--reboot` with create-image on a bake instance -- it resets model cache permissions
- The HF token is fetched from Secrets Manager at bake time to pre-download models; the worker also fetches it at runtime for pyannote
- Model weights live at /opt/cpp/models/ (~80 GB total: whisper large-v3 ~3 GB, alignment models ~500 MB, pyannote ~300 MB)
- The bake SG allows all outbound (for pip + HF downloads) but no inbound; SSM provides access via IAM role
