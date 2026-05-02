#!/bin/bash
# ContextPulse Pipeline — Phase 1 Transcribe-Only worker boot script.
#
# Sibling to boot.sh (which runs the v0.1 full pipeline). This boot script
# is for the deliberately-minimal "transcribe per source, do nothing else"
# variant under contextpulse_pipeline/pipelines/phase1_transcribe/.
#
# Differences from boot.sh:
#   - Does NOT pre-download whisperx alignment model or pyannote diarization
#   - Starts cpp-phase1-transcribe-worker.service (different systemd unit)
#   - Worker reads spec from $PHASE1_SPEC_S3_URI (passed via user-data env)
#
# Same as boot.sh:
#   - DLAMI Ubuntu 22.04 base
#   - Same constraints file (cpp-constraints-2026-04-29-known-good.txt)
#   - Same S3-staged code sync
#   - Same ERR-trap log upload (skill lesson 2026-04-30)
#   - Same logtail service for live diagnostics

set -e
exec > /var/log/cpp-boot-phase1.log 2>&1
echo "=== ContextPulse Phase 1 Transcribe boot starting $(date -u) ==="

mark() {
    echo "BOOT-PROGRESS: $1 at $(date -u)"
    echo "$1" | aws s3 cp - "s3://jerard-activefounder/build-state/cpp-phase1-boot-PROGRESS-$1" --region us-east-1 || true
}

trap 'rc=$?; echo "BOOT FAILED with rc=$rc at $(date -u)"; aws s3 cp /var/log/cpp-boot-phase1.log s3://jerard-activefounder/build-state/cpp-phase1-boot.log --region us-east-1 || true; echo "rc=$rc" | aws s3 cp - s3://jerard-activefounder/build-state/cpp-phase1-boot-FAILED --region us-east-1 || true; shutdown -h +5 || true; exit $rc' ERR

mark "01-started"

apt-get update -y
apt-get install -y ffmpeg git python3.10-venv python3.10-dev

mark "02-apt-done"

mkdir -p /opt/cpp /opt/models /opt/models/hf_cache && chown -R ubuntu /opt/cpp /opt/models
sudo -u ubuntu python3.10 -m venv /opt/cpp/venv
sudo -u ubuntu /opt/cpp/venv/bin/pip install --upgrade pip wheel setuptools

mark "03-venv-ready"

# Slim install — only what transcribe_per_source.py needs.
# (Full boot.sh also pulls whisperx + pyannote for diarization; we skip those.)
aws s3 cp s3://jerard-activefounder/build-state/cpp-constraints-2026-04-29-known-good.txt /tmp/constraints.txt
sudo -u ubuntu /opt/cpp/venv/bin/pip install \
    --extra-index-url https://download.pytorch.org/whl/cu121 \
    --constraint /tmp/constraints.txt \
    torch torchaudio \
    "faster-whisper>=1.0.3" \
    pydantic \
    boto3 numpy soundfile

mark "04-pip-installed"

mkdir -p /opt/cpp/contextpulse_pipeline
aws s3 sync s3://jerard-activefounder/code/contextpulse_pipeline/ /opt/cpp/contextpulse_pipeline/
chown -R ubuntu /opt/cpp

mark "05-code-synced"

# Pre-download Whisper-large-v3 only (no whisperx, no pyannote).
HF_TOKEN=$(aws secretsmanager get-secret-value --secret-id contextpulse/hf_token --query SecretString --output text --region us-east-1)
sudo -u ubuntu HF_TOKEN="$HF_TOKEN" HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" /opt/cpp/venv/bin/python <<'PYEOF'
from faster_whisper import WhisperModel
print('Pre-downloading Whisper-large-v3 (CUDA float16)...')
WhisperModel('large-v3', device='cuda', compute_type='float16')
print('Whisper-large-v3 ready on GPU')
PYEOF

mark "06-models-ready"

# PHASE1_SPEC_S3_URI is passed via user-data (the launcher writes it into
# /etc/cpp-phase1.env which we source into the systemd unit Environment).
# Default path expects the launcher to have created /etc/cpp-phase1.env.
test -f /etc/cpp-phase1.env || { echo "/etc/cpp-phase1.env missing — launcher did not pass spec"; exit 1; }

tee /etc/systemd/system/cpp-phase1-transcribe-worker.service >/dev/null <<EOF
[Unit]
Description=ContextPulse Phase 1 Transcribe Worker
After=network-online.target
[Service]
Type=simple
User=ubuntu
EnvironmentFile=/etc/cpp-phase1.env
ExecStart=/opt/cpp/venv/bin/python -m contextpulse_pipeline.pipelines.phase1_transcribe.worker
ExecStopPost=/usr/bin/bash -c 'TS=\$(date -u +%%Y%%m%%dT%%H%%M%%SZ); journalctl -u cpp-phase1-transcribe-worker.service --no-pager > /tmp/worker-final.log 2>&1; aws s3 cp /tmp/worker-final.log s3://jerard-activefounder/build-state/phase1-worker-journal-\$TS.log --region us-east-1 || true'
Restart=no
TimeoutStopSec=120
Environment=PYTHONPATH=/opt/cpp
Environment=AWS_DEFAULT_REGION=us-east-1
Environment=HF_HOME=/opt/models/hf_cache
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable cpp-phase1-transcribe-worker.service

# Logtail (same pattern as boot.sh; lesson 2026-04-30)
tee /etc/systemd/system/cpp-phase1-logtail.service >/dev/null <<EOF
[Unit]
Description=Phase 1 Worker Log Tailer
After=cpp-phase1-transcribe-worker.service
[Service]
Type=simple
User=ubuntu
ExecStart=/usr/bin/bash -c 'while systemctl is-active --quiet cpp-phase1-transcribe-worker.service || systemctl is-failed --quiet cpp-phase1-transcribe-worker.service; do journalctl -u cpp-phase1-transcribe-worker.service --no-pager > /tmp/worker-live.log 2>&1; aws s3 cp /tmp/worker-live.log s3://jerard-activefounder/build-state/phase1-worker-journal-LIVE.log --region us-east-1 --quiet || true; sleep 60; done'
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable cpp-phase1-logtail.service

# Order matters (lesson 2026-04-30): worker first, then logtail
systemctl start cpp-phase1-transcribe-worker.service
sleep 2
systemctl start cpp-phase1-logtail.service

mark "07-worker-started"

echo "=== Phase 1 Transcribe boot complete $(date -u) ==="
aws s3 cp /var/log/cpp-boot-phase1.log s3://jerard-activefounder/build-state/cpp-phase1-boot.log --region us-east-1 || true
echo "complete" | aws s3 cp - s3://jerard-activefounder/build-state/cpp-phase1-boot-DONE --region us-east-1
