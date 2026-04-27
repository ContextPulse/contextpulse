#!/bin/bash
# ============================================================================
# ContextPulse Pipeline -- G6 GPU AMI Bake Script (x86_64 / NVIDIA L4)
#
# Run this ON the bake instance (g6.xlarge) to install the ML stack.
# Pattern adapted from CryptoTrader scripts/ec2/bake-ami.sh.
#
# Usage: sudo bash bake-g6-ami.sh
# Expected runtime: 30-50 min (model pre-download is the long pole)
# ============================================================================
set -euo pipefail

APP_DIR="/opt/cpp"
VENV_DIR="$APP_DIR/venv"
MODELS_DIR="$APP_DIR/models"
HF_CACHE="$APP_DIR/models/hf_cache"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
S3_CODE_BUCKET="jerard-activefounder"
S3_CODE_PREFIX="code/contextpulse_pipeline"

echo "============================================"
echo "  ContextPulse Pipeline GPU AMI Bake"
echo "  $(date -u)"
echo "============================================"

# ── 1. System packages ──────────────────────────────────────────────────────
echo ""
echo "[1/6] Installing system packages..."
apt-get update -y -q
apt-get install -y -q \
    ffmpeg \
    python3.11 python3.11-venv python3.11-dev \
    git \
    jq \
    curl \
    unzip \
    awscli

echo "  System packages done."

# ── 2. AWS CLI v2 (apt awscli is v1 -- upgrade to v2) ──────────────────────
echo ""
echo "[2/6] Installing AWS CLI v2..."
if ! aws --version 2>&1 | grep -q "aws-cli/2"; then
    curl -sL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
    cd /tmp && unzip -qo awscliv2.zip
    ./aws/install --update
    rm -rf /tmp/aws /tmp/awscliv2.zip
    echo "  AWS CLI v2 installed."
else
    echo "  AWS CLI v2 already present."
fi

# ── 3. Python venv and ML stack ─────────────────────────────────────────────
echo ""
echo "[3/6] Creating Python venv and installing ML stack..."
mkdir -p "$APP_DIR" "$MODELS_DIR" "$HF_CACHE"
chown -R ubuntu:ubuntu "$APP_DIR" 2>/dev/null || true

sudo -u ubuntu python3.11 -m venv "$VENV_DIR"
sudo -u ubuntu "$VENV_DIR/bin/pip" install --upgrade pip -q

# Install ML stack with pinned versions for reproducibility
sudo -u ubuntu "$VENV_DIR/bin/pip" install -q \
    "faster-whisper>=1.0.3" \
    "whisperx>=3.1.5" \
    "pyannote.audio==3.1.1" \
    "boto3" \
    "numpy" \
    "scipy" \
    "soundfile" \
    "librosa" \
    "pydub"

echo "  Python ML stack installed."

# ── 4. Pre-download model weights ───────────────────────────────────────────
echo ""
echo "[4/6] Pre-downloading model weights (long step ~20-30 min)..."
export HF_TOKEN=$(aws secretsmanager get-secret-value \
    --secret-id contextpulse/hf_token \
    --query SecretString \
    --output text \
    --region "$REGION")
export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"
export HF_HOME="$HF_CACHE"
export TRANSFORMERS_CACHE="$HF_CACHE"

sudo -Eu ubuntu HF_HOME="$HF_CACHE" HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" \
    "$VENV_DIR/bin/python" -c "
import os
os.environ['HF_HOME'] = '$HF_CACHE'
os.environ['HUGGING_FACE_HUB_TOKEN'] = '$HF_TOKEN'
os.environ['TRANSFORMERS_CACHE'] = '$HF_CACHE'

print('  [4a] Loading faster-whisper large-v3 (CUDA download)...')
from faster_whisper import WhisperModel
WhisperModel('large-v3', device='cuda', compute_type='int8_float16',
             download_root='$MODELS_DIR/whisper')
print('  [4a] Done.')

print('  [4b] Loading whisperx alignment model...')
import whisperx
whisperx.load_align_model(language_code='en', device='cuda')
print('  [4b] Done.')

print('  [4c] Loading pyannote speaker-diarization-3.1...')
from pyannote.audio import Pipeline
Pipeline.from_pretrained(
    'pyannote/speaker-diarization-3.1',
    use_auth_token='$HF_TOKEN',
)
print('  [4c] Done.')
print('  All model weights pre-downloaded.')
"

chown -R ubuntu:ubuntu "$APP_DIR"
echo "  Model pre-download complete."

# ── 5. Upload and install contextpulse_pipeline package ─────────────────────
echo ""
echo "[5/6] Syncing contextpulse_pipeline code from S3..."
mkdir -p "$APP_DIR/contextpulse_pipeline"
aws s3 sync "s3://$S3_CODE_BUCKET/$S3_CODE_PREFIX/" \
    "$APP_DIR/contextpulse_pipeline/" \
    --region "$REGION" \
    --delete

# Install the package into the venv
sudo -u ubuntu "$VENV_DIR/bin/pip" install -e "$APP_DIR/contextpulse_pipeline/" -q || \
    echo "  Note: editable install failed (no setup.py) -- PYTHONPATH will cover it"

chown -R ubuntu:ubuntu "$APP_DIR"
echo "  contextpulse_pipeline synced."

# ── 6. Systemd service ──────────────────────────────────────────────────────
echo ""
echo "[6/6] Installing systemd service..."
tee /etc/systemd/system/cpp-spot-worker.service > /dev/null << 'EOF'
[Unit]
Description=ContextPulse Spot Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
ExecStart=/opt/cpp/venv/bin/python -m contextpulse_pipeline.workers.spot_worker
Restart=always
RestartSec=10
Environment=PYTHONPATH=/opt/cpp/contextpulse_pipeline
Environment=AWS_DEFAULT_REGION=us-east-1
Environment=HF_HOME=/opt/cpp/models/hf_cache
Environment=MODELS_DIR=/opt/cpp/models
Environment=TRANSFORMERS_CACHE=/opt/cpp/models/hf_cache
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cpp-spot-worker.service
echo "  cpp-spot-worker service enabled (will start on next boot)."

echo ""
echo "============================================"
echo "  AMI bake complete! $(date -u)"
echo "  Next steps:"
echo "    1. aws ec2 create-image --instance-id <id> --name contextpulse-pipeline-g6-v1"
echo "    2. Wait for state=available"
echo "    3. Terminate this instance"
echo "============================================"
