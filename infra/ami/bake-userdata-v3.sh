#!/bin/bash
# ContextPulse Pipeline AMI bake — self-completing user-data (v3)
# Differences from handoff doc v1/v2:
#   - python3.10 (Ubuntu 22.04 default) instead of python3.11 (deadsnakes)
#   - explicit CUDA-enabled torch install before ML libs
#   - progress markers in s3://jerard-activefounder/build-state/
#   - ERR trap writes FAILED marker + uploads log for diagnosis
set -e
exec > /var/log/cpp-bake.log 2>&1
echo "=== ContextPulse AMI Bake Starting $(date -u) ==="
echo "Instance: $(hostname)"

mark() {
    echo "PROGRESS: $1 at $(date -u)"
    echo "$1" | aws s3 cp - "s3://jerard-activefounder/build-state/cpp-ami-bake-PROGRESS-$1" --region us-east-1 || true
}

trap 'rc=$?; echo "FAILED with rc=$rc at $(date -u)"; aws s3 cp /var/log/cpp-bake.log s3://jerard-activefounder/build-state/cpp-ami-bake.log --region us-east-1 || true; echo "rc=$rc" | aws s3 cp - s3://jerard-activefounder/build-state/cpp-ami-bake-FAILED --region us-east-1 || true; shutdown -h +1 || true; exit $rc' ERR

mark "01-started"

# DLAMI Ubuntu 22.04 has python3.10 + awscli v2 pre-installed
apt-get update -y
apt-get install -y ffmpeg git python3.10-venv python3.10-dev

mark "02-apt-done"

mkdir -p /opt/cpp && chown ubuntu /opt/cpp
sudo -u ubuntu python3.10 -m venv /opt/cpp/venv
sudo -u ubuntu /opt/cpp/venv/bin/pip install --upgrade pip wheel setuptools

mark "03-venv-ready"

# CUDA-enabled PyTorch (cu121 wheels work fine on 12.x drivers)
sudo -u ubuntu /opt/cpp/venv/bin/pip install \
    torch==2.3.1 torchaudio==2.3.1 \
    --index-url https://download.pytorch.org/whl/cu121

mark "04-torch-installed"

sudo -u ubuntu /opt/cpp/venv/bin/pip install \
    "faster-whisper>=1.0.3" \
    "whisperx>=3.1.5" \
    "pyannote.audio>=3.3.2,<4.0" \
    boto3 numpy scipy soundfile librosa pydub

mark "05-mllibs-installed"

mkdir -p /opt/cpp/contextpulse_pipeline
aws s3 sync s3://jerard-activefounder/code/contextpulse_pipeline/ /opt/cpp/contextpulse_pipeline/
chown -R ubuntu /opt/cpp

mark "06-code-synced"

# Pre-download all models so first job doesn't bottleneck on download
HF_TOKEN=$(aws secretsmanager get-secret-value --secret-id contextpulse/hf_token --query SecretString --output text --region us-east-1)
sudo -u ubuntu HF_TOKEN="$HF_TOKEN" HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" /opt/cpp/venv/bin/python <<'PYEOF'
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
PYEOF

mark "07-models-downloaded"

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

mark "08-service-enabled"

echo "=== Bake Complete $(date -u) ==="
aws s3 cp /var/log/cpp-bake.log s3://jerard-activefounder/build-state/cpp-ami-bake.log --region us-east-1 || true
echo "complete" | aws s3 cp - s3://jerard-activefounder/build-state/cpp-ami-bake-DONE --region us-east-1
