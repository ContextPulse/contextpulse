#!/bin/bash
# ContextPulse Pipeline — per-boot install + worker start
# Runs at every worker boot. Pulled from S3 by user-data (vanilla DLAMI base).
#
# Why this exists: 7 attempts to bake a heavy AMI all failed at different layers
# (disk, pip resolution, gated HF models, pyannote 4.x API breakages, IAM gaps).
# Foreground iteration on a dev box found the working config in 30 min.
# This boot script captures that working config; ~10 min per worker boot is
# acceptable trade vs the bake-debug-for-hours loop.
#
# Pattern: light AMI (DLAMI as-is) + this boot.sh + S3-stored constraints.
# Updates ship by S3 sync — no AMI rebuild required.

set -e
exec > /var/log/cpp-boot.log 2>&1
echo "=== ContextPulse boot starting $(date -u) ==="

mark() {
    echo "BOOT-PROGRESS: $1 at $(date -u)"
    echo "$1" | aws s3 cp - "s3://jerard-activefounder/build-state/cpp-boot-PROGRESS-$1" --region us-east-1 || true
}

trap 'rc=$?; echo "BOOT FAILED with rc=$rc at $(date -u)"; aws s3 cp /var/log/cpp-boot.log s3://jerard-activefounder/build-state/cpp-boot.log --region us-east-1 || true; echo "rc=$rc" | aws s3 cp - s3://jerard-activefounder/build-state/cpp-boot-FAILED --region us-east-1 || true; shutdown -h +5 || true; exit $rc' ERR

mark "01-started"

# DLAMI Ubuntu 22.04 already has python3.10, awscli v2, NVIDIA drivers, CUDA
# We install ffmpeg + venv tooling on top.
apt-get update -y
apt-get install -y ffmpeg git python3.10-venv python3.10-dev

mark "02-apt-done"

# Fresh venv each boot (cheap, ~1 sec)
# /opt/models holds HF cache + Whisper weights (worker reads MODELS_DIR env)
mkdir -p /opt/cpp /opt/models /opt/models/hf_cache && chown -R ubuntu /opt/cpp /opt/models
sudo -u ubuntu python3.10 -m venv /opt/cpp/venv
sudo -u ubuntu /opt/cpp/venv/bin/pip install --upgrade pip wheel setuptools

mark "03-venv-ready"

# Pull constraints + install in one go.
# --extra-index-url ensures torch+cu121 wheels (CUDA-enabled, not CPU)
# --constraint pins all 143 transitive deps to the verified-2026-04-29 set
aws s3 cp s3://jerard-activefounder/build-state/cpp-constraints-2026-04-29-known-good.txt /tmp/constraints.txt
sudo -u ubuntu /opt/cpp/venv/bin/pip install \
    --extra-index-url https://download.pytorch.org/whl/cu121 \
    --constraint /tmp/constraints.txt \
    torch torchaudio \
    "faster-whisper>=1.0.3" \
    whisperx \
    pyannote.audio \
    pydantic \
    boto3 numpy scipy soundfile librosa pydub

mark "04-pip-installed"

# Sync latest pipeline code from S3 (always pull HEAD — boot.sh is the only
# baked-in thing; code itself iterates without instance churn)
mkdir -p /opt/cpp/contextpulse_pipeline
aws s3 sync s3://jerard-activefounder/code/contextpulse_pipeline/ /opt/cpp/contextpulse_pipeline/
chown -R ubuntu /opt/cpp

mark "05-code-synced"

# Pre-download models so first SQS job doesn't bottleneck on HF.
# Token from Secrets Manager (instance role permits it).
HF_TOKEN=$(aws secretsmanager get-secret-value --secret-id contextpulse/hf_token --query SecretString --output text --region us-east-1)
sudo -u ubuntu HF_TOKEN="$HF_TOKEN" HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" /opt/cpp/venv/bin/python <<'PYEOF'
import os, torch
from faster_whisper import WhisperModel
import whisperx
from pyannote.audio import Pipeline

token = os.environ['HF_TOKEN']
print('Pre-downloading Whisper-large-v3...')
WhisperModel('large-v3', device='cpu', compute_type='int8')
print('Pre-downloading WhisperX align model...')
whisperx.load_align_model(language_code='en', device='cpu')
print('Pre-downloading pyannote diarization 3.1 (pyannote-audio 4.x API: token=, not use_auth_token=)...')
Pipeline.from_pretrained('pyannote/speaker-diarization-3.1', token=token)
print('All models pre-downloaded')
PYEOF

mark "06-models-ready"

# Systemd unit for the worker (idempotent)
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
systemctl start cpp-spot-worker.service

mark "07-worker-started"

echo "=== Boot complete $(date -u) ==="
aws s3 cp /var/log/cpp-boot.log s3://jerard-activefounder/build-state/cpp-boot.log --region us-east-1 || true
echo "complete" | aws s3 cp - s3://jerard-activefounder/build-state/cpp-boot-DONE --region us-east-1
