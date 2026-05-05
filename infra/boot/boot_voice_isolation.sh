#!/bin/bash
# ContextPulse Pipeline — Stage 6 Voice Isolation worker boot script.
#
# Sibling to boot_phase1_5_fingerprint.sh. Extends the slim install with:
#   - speechbrain (ECAPA-TDNN — same as phase1_5; needed by WeSepExtractor's
#     internal embedding lookups)
#   - wesep (target speaker extraction)
#
# Same as boot_phase1_5_fingerprint.sh:
#   - DLAMI Ubuntu 22.04 base
#   - cpp-constraints-2026-04-29-known-good.txt
#   - S3-staged code sync
#   - ERR-trap log upload (skill lesson 2026-04-30)
#   - Logtail-after-worker ordering (skill lesson 2026-04-30)

set -e
exec > /var/log/cpp-boot-voice-isolation.log 2>&1
echo "=== ContextPulse Voice Isolation boot starting $(date -u) ==="

mark() {
    echo "BOOT-PROGRESS: $1 at $(date -u)"
    echo "$1" | aws s3 cp - "s3://jerard-activefounder/build-state/cpp-voice-iso-boot-PROGRESS-$1" --region us-east-1 || true
}

trap 'rc=$?; echo "BOOT FAILED with rc=$rc at $(date -u)"; aws s3 cp /var/log/cpp-boot-voice-isolation.log s3://jerard-activefounder/build-state/cpp-voice-iso-boot.log --region us-east-1 || true; echo "rc=$rc" | aws s3 cp - s3://jerard-activefounder/build-state/cpp-voice-iso-boot-FAILED --region us-east-1 || true; shutdown -h +5 || true; exit $rc' ERR

mark "01-started"

apt-get update -y
apt-get install -y ffmpeg git python3.10-venv python3.10-dev

mark "02-apt-done"

mkdir -p /opt/cpp /opt/models /opt/models/hf_cache /opt/models/wesep && chown -R ubuntu /opt/cpp /opt/models
sudo -u ubuntu python3.10 -m venv /opt/cpp/venv
sudo -u ubuntu /opt/cpp/venv/bin/pip install --upgrade pip wheel setuptools

mark "03-venv-ready"

aws s3 cp s3://jerard-activefounder/build-state/cpp-constraints-2026-04-29-known-good.txt /tmp/constraints.txt
sudo -u ubuntu /opt/cpp/venv/bin/pip install \
    --extra-index-url https://download.pytorch.org/whl/cu121 \
    --constraint /tmp/constraints.txt \
    torch torchaudio \
    "speechbrain>=1.0,<2.0" \
    "scikit-learn>=1.3" \
    "scipy>=1.11" \
    pydantic \
    boto3 numpy soundfile

# WeSep is a research repo (GitHub-only, not on PyPI). Install separately
# from the constraints-pinned step above. Skill self-learning rule
# (building-transcription-pipelines, 2026-05-03): "WeSep is a research repo
# (GitHub-only), not on PyPI -- use pip install wesep @ git+https://...".
sudo -u ubuntu /opt/cpp/venv/bin/pip install \
    "wesep @ git+https://github.com/wenet-e2e/wesep@main"

mark "04-pip-installed"

mkdir -p /opt/cpp/contextpulse_pipeline
aws s3 sync s3://jerard-activefounder/code/contextpulse_pipeline/ /opt/cpp/contextpulse_pipeline/
chown -R ubuntu /opt/cpp

mark "05-code-synced"

# Pre-download WeSep model so first .extract() doesn't pay a cold fetch.
HF_TOKEN=$(aws secretsmanager get-secret-value --secret-id contextpulse/hf_token --query SecretString --output text --region us-east-1)
sudo -u ubuntu HF_TOKEN="$HF_TOKEN" HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" /opt/cpp/venv/bin/python <<'PYEOF'
import os
os.environ.setdefault("HF_HOME", "/opt/models/hf_cache")
print('Pre-downloading WeSep target speaker extraction model...')
try:
    from wesep.cli.utils import load_pretrained_model
    load_pretrained_model(
        "Wespeaker/wespeaker-voxceleb-resnet34",
        savedir="/opt/models/wesep",
        device="cuda",
    )
    print('WeSep model ready on GPU')
except Exception as exc:
    print(f'WeSep pre-download failed (non-fatal — worker will retry): {exc}')
PYEOF

mark "06-models-ready"

test -f /etc/cpp-voice-isolation.env || { echo "/etc/cpp-voice-isolation.env missing"; exit 1; }

tee /etc/systemd/system/cpp-voice-isolation-worker.service >/dev/null <<EOF
[Unit]
Description=ContextPulse Voice Isolation Worker
After=network-online.target
[Service]
Type=simple
User=ubuntu
EnvironmentFile=/etc/cpp-voice-isolation.env
ExecStart=/opt/cpp/venv/bin/python -m contextpulse_pipeline.pipelines.voice_isolation.worker
ExecStopPost=/usr/bin/bash -c 'TS=\$(date -u +%%Y%%m%%dT%%H%%M%%SZ); journalctl -u cpp-voice-isolation-worker.service --no-pager > /tmp/worker-final.log 2>&1; aws s3 cp /tmp/worker-final.log s3://jerard-activefounder/build-state/voice-iso-worker-journal-\$TS.log --region us-east-1 || true'
Restart=no
TimeoutStopSec=120
Environment=PYTHONPATH=/opt/cpp
Environment=AWS_DEFAULT_REGION=us-east-1
Environment=HF_HOME=/opt/models/hf_cache
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable cpp-voice-isolation-worker.service

tee /etc/systemd/system/cpp-voice-iso-logtail.service >/dev/null <<EOF
[Unit]
Description=Voice Isolation Worker Log Tailer
After=cpp-voice-isolation-worker.service
[Service]
Type=simple
User=ubuntu
ExecStart=/usr/bin/bash -c 'while systemctl is-active --quiet cpp-voice-isolation-worker.service || systemctl is-failed --quiet cpp-voice-isolation-worker.service; do journalctl -u cpp-voice-isolation-worker.service --no-pager > /tmp/worker-live.log 2>&1; aws s3 cp /tmp/worker-live.log s3://jerard-activefounder/build-state/voice-iso-worker-journal-LIVE.log --region us-east-1 --quiet || true; sleep 60; done'
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable cpp-voice-iso-logtail.service

systemctl start cpp-voice-isolation-worker.service
sleep 2
systemctl start cpp-voice-iso-logtail.service

mark "07-worker-started"

echo "=== Voice Isolation boot complete $(date -u) ==="
aws s3 cp /var/log/cpp-boot-voice-isolation.log s3://jerard-activefounder/build-state/cpp-voice-iso-boot.log --region us-east-1 || true
echo "complete" | aws s3 cp - s3://jerard-activefounder/build-state/cpp-voice-iso-boot-DONE --region us-east-1
