# Phase 3 GPU Spot Transcription Pipeline — Handoff

**Status:** Infrastructure ~70% complete. AMI bake + launch template + Josh end-to-end test still pending. Multiple prior agent attempts on 2026-04-26 and overnight failed due to tool-budget exhaustion while babysitting the bake.

**Created:** 2026-04-27, after the overnight failure.
**Next session task:** complete buckets 1-7 below using the **self-completing user-data pattern** (no agent babysitting required).

---

## Goal

Per `~/Projects/ContextPulse/docs/VOICE_PIPELINE_ARCHITECTURE.md` and the `building-transcription-pipelines` skill: ship the production GPU spot transcription + diarization architecture for ActiveFounder + future voice products (ContextPulse Cloud, ServiceDocs AI).

**Locked technical decisions** (do not re-debate):
- STT: Whisper-large-v3 via faster-whisper (CTranslate2, int8_float16)
- Forced alignment: WhisperX (wav2vec2)
- Diarization: pyannote 3.1
- GPU primary: inf2.xlarge spot (Phase 3.1, Neuron compile is 1-2 days — not in scope here)
- **GPU for tonight: g6.xlarge spot** (~$0.40/hr)
- Worker pattern: SQS-driven, manifest-based, idempotent, self-terminating after idle timeout

---

## What's already done (verified 2026-04-27 14:30 MT)

| Component | State | Reference |
|---|---|---|
| IAM role | `contextpulse-transcription-worker-role` | `aws iam list-roles \| grep contextpulse` |
| Instance profile | `contextpulse-transcription-worker-profile` | `aws iam list-instance-profiles \| grep contextpulse` |
| SQS queue | `https://sqs.us-east-1.amazonaws.com/397348547231/contextpulse-transcription-queue` | |
| SQS DLQ | `https://sqs.us-east-1.amazonaws.com/397348547231/contextpulse-transcription-dlq` | |
| HuggingFace token | Secrets Manager: `contextpulse/hf_token` (ARN ends `Kk9tC5`) | pyannote-3.1 terms accepted by user |
| Worker daemon code | `~/Projects/ContextPulse/contextpulse_pipeline/workers/spot_worker.py` | SQS poll + transcribe + diarize + master.py Tier 1 + mix + upload + idle-self-terminate |
| Master.py (Tier 1) | `~/Projects/ContextPulse/contextpulse_pipeline/master.py` | concat + highpass + denoise + level-match + bleed-cancel + transcript merge + QC. Tests broken on synthetic OGG fixture but logic appears sound. |
| Step Functions ASL | `UnifyAudio` state inserted in `~/Projects/ActiveFounder/aws/stepfunctions/activefounder-pipeline.asl.json` | `grep -c UnifyAudio …asl.json` returns 12 |
| Skill | `~/.claude/skills/enhancing-audio-for-podcasts/SKILL.md` | mirrored to `~/.agent/` clean |
| Skill | `~/.claude/skills/building-transcription-pipelines/SKILL.md` | the orchestrator |
| Quota | G/VT spot = 8 vCPUs (g6.xlarge needs 4 — fits) | request for 16 still `CASE_OPENED` (case 177725459300781), AWS auto-approved 4→8, may bump further |

## What's missing (your job)

| Component | State |
|---|---|
| AMI registered | NONE |
| Launch template | NONE |
| Bake instance running | NONE (prior bake instance `i-0bfdd5d135c6dcf03` died overnight without producing AMI) |
| Josh master files in S3 | NONE |

---

## What went wrong overnight

1. Prior agent (a482f5e82fa00ddd8) launched bake instance `i-0bfdd5d135c6dcf03` at 2026-04-27T01:59:24 UTC.
2. Agent stopped to "wait for bake to progress" but tool budget exhausted before snapshot.
3. Babysitter agent (a485081e52675728f) launched after but never completed — no journal entries from it overnight.
4. Bake instance terminated without producing AMI (unclear why — possibly self-terminate, spot interruption, or babysitter agent killed it without snapshotting).
5. EC2 compute cost yesterday: $0 — confirms no instance ran for any meaningful time.

**Root cause:** agents repeatedly drop at small mechanical points (Docker missing, deploy.py edits, IAM tweaks) and run out of tool budget while waiting on slow async operations (AMI bake takes 30-60 min, agent polls eat budget).

**The fix:** **self-completing user-data with a S3 marker.** Bake instance does the full install in user-data. When done, writes `s3://jerard-activefounder/build-state/cpp-ami-bake-DONE` marker. Agent just polls for the marker via cheap S3 list calls. No SSM tunneling, no console-output parsing, no "wait for instance to be ready."

---

## Next-session execution plan (7 buckets)

### Bucket 1 — Stage source code in S3 (~5 min)

```bash
cd ~/Projects/ContextPulse
aws s3 sync contextpulse_pipeline/ s3://jerard-activefounder/code/contextpulse_pipeline/ \
  --delete \
  --exclude "__pycache__/*" --exclude "*.pyc" --exclude ".pytest_cache/*"
```

### Bucket 2 — Launch self-completing bake instance (~5 min to launch, then 30-60 min unattended)

Create `/tmp/bake-userdata.sh`:

```bash
#!/bin/bash
set -e
exec > /var/log/cpp-bake.log 2>&1
echo "=== ContextPulse AMI Bake Starting $(date -u) ==="

apt-get update -y
apt-get install -y ffmpeg python3.11 python3.11-venv git awscli

mkdir -p /opt/cpp && chown ubuntu /opt/cpp
sudo -u ubuntu python3.11 -m venv /opt/cpp/venv
sudo -u ubuntu /opt/cpp/venv/bin/pip install --upgrade pip wheel setuptools

sudo -u ubuntu /opt/cpp/venv/bin/pip install \
  "faster-whisper>=1.0.3" \
  "whisperx>=3.1.5" \
  "pyannote.audio==3.1.1" \
  boto3 numpy scipy soundfile librosa pydub

mkdir -p /opt/cpp/contextpulse_pipeline
aws s3 sync s3://jerard-activefounder/code/contextpulse_pipeline/ /opt/cpp/contextpulse_pipeline/
chown -R ubuntu /opt/cpp

HF_TOKEN=$(aws secretsmanager get-secret-value --secret-id contextpulse/hf_token --query SecretString --output text --region us-east-1)
sudo -u ubuntu HF_TOKEN="$HF_TOKEN" HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" /opt/cpp/venv/bin/python -c "
import os
hf_token = os.environ['HF_TOKEN']
print('Downloading Whisper-large-v3...')
from faster_whisper import WhisperModel
WhisperModel('large-v3', device='cpu', compute_type='int8')
print('Downloading WhisperX align model...')
import whisperx
whisperx.load_align_model(language_code='en', device='cpu')
print('Downloading pyannote 3.1...')
from pyannote.audio import Pipeline
Pipeline.from_pretrained('pyannote/speaker-diarization-3.1', use_auth_token=hf_token)
print('All models pre-downloaded')
"

tee /etc/systemd/system/cpp-spot-worker.service >/dev/null <<EOF
[Unit]
Description=ContextPulse Spot Worker
After=network-online.target
[Service]
Type=simple
User=ubuntu
ExecStart=/opt/cpp/venv/bin/python -m contextpulse_pipeline.workers.spot_worker
Restart=always
RestartSec=10
Environment=PYTHONPATH=/opt/cpp
Environment=AWS_DEFAULT_REGION=us-east-1
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable cpp-spot-worker.service

echo "=== Bake Complete $(date -u) ==="
aws s3 cp /var/log/cpp-bake.log s3://jerard-activefounder/build-state/cpp-ami-bake.log --region us-east-1 || true
echo "complete" | aws s3 cp - s3://jerard-activefounder/build-state/cpp-ami-bake-DONE --region us-east-1
```

Launch:
```bash
BASE_AMI=$(aws ec2 describe-images --owners amazon \
  --filters "Name=name,Values=Deep Learning OSS Nvidia Driver AMI GPU PyTorch*Ubuntu 22.04*" \
  --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text)

aws ec2 run-instances \
  --image-id $BASE_AMI \
  --instance-type g6.xlarge \
  --iam-instance-profile Name=contextpulse-transcription-worker-profile \
  --user-data file:///tmp/bake-userdata.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=cpp-ami-bake-v3},{Key=Project,Value=ContextPulsePipeline},{Key=Stage,Value=bake}]' \
  --instance-initiated-shutdown-behavior stop \
  --query "Instances[0].InstanceId" --output text
```

### Bucket 3 — Poll for completion marker

Cheap S3 list every 5 min, max 24 iterations (2h):
```bash
for i in $(seq 1 24); do
  if aws s3 ls s3://jerard-activefounder/build-state/cpp-ami-bake-DONE 2>/dev/null; then
    echo "BAKE DONE on iteration $i"
    break
  fi
  echo "Iteration $i: still baking"
  sleep 300
done
```

If after 2h no marker: `aws s3 cp s3://jerard-activefounder/build-state/cpp-ami-bake.log -` for partial log; check instance state. Do NOT loop indefinitely — report and stop.

### Bucket 4 — Snapshot to AMI + terminate bake

```bash
AMI_ID=$(aws ec2 create-image \
  --instance-id <bake-instance-id> \
  --name "contextpulse-pipeline-g6-v1" \
  --description "ContextPulse Pipeline GPU spot worker AMI v1 (faster-whisper + WhisperX + pyannote 3.1, models pre-downloaded)" \
  --no-reboot \
  --query ImageId --output text)
aws ec2 wait image-available --image-ids $AMI_ID
aws ec2 terminate-instances --instance-ids <bake-instance-id>
```

### Bucket 5 — Launch template

```bash
USER_DATA=$(echo -n '#!/bin/bash
systemctl start cpp-spot-worker.service' | base64 -w0)

aws ec2 create-launch-template \
  --launch-template-name contextpulse-pipeline-g6-launch-template-v1 \
  --launch-template-data "{
    \"ImageId\": \"$AMI_ID\",
    \"InstanceType\": \"g6.xlarge\",
    \"IamInstanceProfile\": {\"Name\": \"contextpulse-transcription-worker-profile\"},
    \"InstanceMarketOptions\": {
      \"MarketType\": \"spot\",
      \"SpotOptions\": {\"MaxPrice\": \"1.00\", \"InstanceInterruptionBehavior\": \"terminate\"}
    },
    \"UserData\": \"$USER_DATA\",
    \"InstanceInitiatedShutdownBehavior\": \"terminate\",
    \"TagSpecifications\": [{\"ResourceType\": \"instance\", \"Tags\": [
      {\"Key\": \"Project\", \"Value\": \"ContextPulsePipeline\"},
      {\"Key\": \"Component\", \"Value\": \"TranscriptionWorker\"},
      {\"Key\": \"Stage\", \"Value\": \"v1\"}
    ]}]
  }"
```

### Bucket 6 — End-to-end Josh test (~15-25 min)

1. Read `~/Projects/ContextPulse/contextpulse_pipeline/workers/spot_worker.py` for the **exact** SQS job schema. Don't guess — match the keys.
2. Submit job:
   ```bash
   aws sqs send-message \
     --queue-url https://sqs.us-east-1.amazonaws.com/397348547231/contextpulse-transcription-queue \
     --message-body '{"session_id":"ep-2026-04-26-josh-cashman","s3_bucket":"jerard-activefounder","audio_prefix":"raw/ep-2026-04-26-josh-cashman/dji/","speaker_mapping":{"TX01":"Josh","TX00":"Chris","ambient":"David"},"output_prefix":"outputs/ep-2026-04-26-josh-cashman"}'
   ```
3. Launch worker:
   ```bash
   aws ec2 run-instances --launch-template "LaunchTemplateName=contextpulse-pipeline-g6-launch-template-v1,Version=\$Latest" --count 1
   ```
4. Poll for outputs (15 × 2 min = 30 min max):
   ```bash
   for i in $(seq 1 15); do
     if aws s3 ls s3://jerard-activefounder/outputs/ep-2026-04-26-josh-cashman/master_qc.json 2>/dev/null; then
       echo "MASTER FILES PRESENT"; break
     fi
     sleep 120
   done
   ```
5. Verify:
   - `master_enhanced.mp3` (~3.5h duration)
   - `master_transcript.md` with Josh/Chris/David per line
   - `master_qc.json` reports clean (sync_drift_ms < 50)
   - Worker self-terminated after idle (`describe-instances` returns empty for `Stage=v1`)

### Bucket 7 — Log + report

```bash
python ~/.claude/shared-knowledge/scripts/log-entry.py --type action-completed --project ContextPulse --content "Phase 3 GPU spot pipeline LIVE: AMI <id>, launch template contextpulse-pipeline-g6-launch-template-v1, Josh master files in S3. Cost <2."
```

---

## Cost expectations

- Bake (g6.xlarge × ~1h): ~$0.40
- Josh test (g6.xlarge × ~25 min): ~$0.20
- AMI snapshot storage: ~$0.05/month for ~5GB (negligible)
- **Total: ~$0.60-0.80** for the build + first run

Hard cap: $5. If anything looks runaway, terminate.

---

## Hard rules (carry into next session)

- **No Replicate, no HuggingFace Inference API, no third-party hosted ML.** Self-hosted GPU spot per locked architecture.
- **Don't republish ep-2026-04-26-josh-cashman.** Master files only. Publish waits on Josh's approval per trailhead contract.
- **Don't touch ep-2026-04-12-bear-peak.**
- **Don't punt.** If a bucket fails, diagnose with logs + report. Don't skip ahead.
- **Reference CryptoTrader's spot pattern** at `~/Projects/CryptoTrader/scripts/ec2/` if launching directly via boto3.

---

## What happens after Phase 3 completes

1. Josh master files exist (`master_enhanced.mp3` + `master_transcript.md`)
2. Run podcast-assembly skill (already shipped) on those master files → 15-25 min mp3 cut
3. Compose email package (David → Chris → Josh per trailhead contract) using `communicating-emails` skill
4. User sends to Chris from JV account
5. HOLD republish until Josh approves redlines

After Josh approves:
6. Move `published.json.unpublished-2026-04-26` back to `published.json`
7. Move Josh photos from `media/ep-2026-04-26-josh-cashman/photos/` (currently archived in this prefix from cleanup) into the right place for the rebuilt episode page
8. Rebuild episodes index
9. Verify card live on activefounder.ai

## Phase 3.1 (separate, ready in 1-2 days after first kickoff)

Bake the Inferentia AMI (`contextpulse-pipeline-inf2-v1`):
- Base: AWS Neuron AMI for Inferentia2
- Same Python stack, but Whisper compiled for Neuron via `optimum-neuron`
- Neuron compile takes 1-2 days (this is the "1-2 day Neuron compilation cost" in the architecture doc)
- Once available: update launch template to prefer inf2 with g6 fallback (3× cheaper at scale)
- Document at `infra/ami/bake-inf2-ami.md`
