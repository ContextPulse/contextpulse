#!/bin/bash
# ContextPulse Pipeline — Phase 1.5 ECAPA Fingerprinting worker boot script.
#
# Sibling to boot_phase1_transcribe.sh. Extends that minimal install with:
#   - speechbrain (ECAPA-TDNN model wrapper)
#   - scikit-learn (for the AgglomerativeClustering import path used by
#     contextpulse_pipeline.speaker_fingerprint at runtime)
#
# Same as boot_phase1_transcribe.sh:
#   - DLAMI Ubuntu 22.04 base
#   - Same constraints file (cpp-constraints-2026-04-29-known-good.txt)
#   - Same S3-staged code sync
#   - Same ERR-trap log upload (skill lesson 2026-04-30)
#   - Same logtail service for live diagnostics
#   - Worker reads spec from $PHASE1_5_SPEC_S3_URI (passed via user-data env)

set -e
exec > /var/log/cpp-boot-phase1-5.log 2>&1
echo "=== ContextPulse Phase 1.5 Fingerprinting boot starting $(date -u) ==="

mark() {
    echo "BOOT-PROGRESS: $1 at $(date -u)"
    echo "$1" | aws s3 cp - "s3://jerard-activefounder/build-state/cpp-phase1-5-boot-PROGRESS-$1" --region us-east-1 || true
}

trap 'rc=$?; echo "BOOT FAILED with rc=$rc at $(date -u)"; aws s3 cp /var/log/cpp-boot-phase1-5.log s3://jerard-activefounder/build-state/cpp-phase1-5-boot.log --region us-east-1 || true; echo "rc=$rc" | aws s3 cp - s3://jerard-activefounder/build-state/cpp-phase1-5-boot-FAILED --region us-east-1 || true; shutdown -h +5 || true; exit $rc' ERR

mark "01-started"

apt-get update -y
apt-get install -y ffmpeg git python3.10-venv python3.10-dev

mark "02-apt-done"

mkdir -p /opt/cpp /opt/models /opt/models/hf_cache /opt/models/spkrec && chown -R ubuntu /opt/cpp /opt/models
sudo -u ubuntu python3.10 -m venv /opt/cpp/venv
sudo -u ubuntu /opt/cpp/venv/bin/pip install --upgrade pip wheel setuptools

mark "03-venv-ready"

# speechbrain pulls torch (already constrained), torchaudio, hyperpyyaml,
# huggingface_hub, sentencepiece. scikit-learn is needed by the orchestrator.
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

mark "04-pip-installed"

mkdir -p /opt/cpp/contextpulse_pipeline
aws s3 sync s3://jerard-activefounder/code/contextpulse_pipeline/ /opt/cpp/contextpulse_pipeline/
chown -R ubuntu /opt/cpp

mark "05-code-synced"

# Pre-download the ECAPA-TDNN model so first .embed() call doesn't pay a
# cold HuggingFace fetch under the worker's runtime budget.
HF_TOKEN=$(aws secretsmanager get-secret-value --secret-id contextpulse/hf_token --query SecretString --output text --region us-east-1)
sudo -u ubuntu HF_TOKEN="$HF_TOKEN" HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" /opt/cpp/venv/bin/python <<'PYEOF'
import os
os.environ.setdefault("HF_HOME", "/opt/models/hf_cache")
print('Pre-downloading ECAPA-TDNN (speechbrain/spkrec-ecapa-voxceleb)...')
from speechbrain.inference.speaker import EncoderClassifier
m = EncoderClassifier.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb",
    savedir="/opt/models/spkrec",
    run_opts={"device": "cuda"},
)
print('ECAPA-TDNN ready on GPU')
PYEOF

mark "06-models-ready"

# PHASE1_5_SPEC_S3_URI is passed via user-data
test -f /etc/cpp-phase1-5.env || { echo "/etc/cpp-phase1-5.env missing — launcher did not pass spec"; exit 1; }

tee /etc/systemd/system/cpp-phase1-5-fingerprint-worker.service >/dev/null <<EOF
[Unit]
Description=ContextPulse Phase 1.5 Fingerprinting Worker
After=network-online.target
[Service]
Type=simple
User=ubuntu
EnvironmentFile=/etc/cpp-phase1-5.env
ExecStart=/opt/cpp/venv/bin/python -m contextpulse_pipeline.pipelines.phase1_5_fingerprint.worker
ExecStopPost=/usr/bin/bash -c 'TS=\$(date -u +%%Y%%m%%dT%%H%%M%%SZ); journalctl -u cpp-phase1-5-fingerprint-worker.service --no-pager > /tmp/worker-final.log 2>&1; aws s3 cp /tmp/worker-final.log s3://jerard-activefounder/build-state/phase1-5-worker-journal-\$TS.log --region us-east-1 || true'
Restart=no
TimeoutStopSec=120
Environment=PYTHONPATH=/opt/cpp
Environment=AWS_DEFAULT_REGION=us-east-1
Environment=HF_HOME=/opt/models/hf_cache
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable cpp-phase1-5-fingerprint-worker.service

# Logtail (same pattern as boot_phase1_transcribe.sh; lesson 2026-04-30)
tee /etc/systemd/system/cpp-phase1-5-logtail.service >/dev/null <<EOF
[Unit]
Description=Phase 1.5 Worker Log Tailer
After=cpp-phase1-5-fingerprint-worker.service
[Service]
Type=simple
User=ubuntu
ExecStart=/usr/bin/bash -c 'while systemctl is-active --quiet cpp-phase1-5-fingerprint-worker.service || systemctl is-failed --quiet cpp-phase1-5-fingerprint-worker.service; do journalctl -u cpp-phase1-5-fingerprint-worker.service --no-pager > /tmp/worker-live.log 2>&1; aws s3 cp /tmp/worker-live.log s3://jerard-activefounder/build-state/phase1-5-worker-journal-LIVE.log --region us-east-1 --quiet || true; sleep 60; done'
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable cpp-phase1-5-logtail.service

# Order matters (lesson 2026-04-30): worker first, then logtail
systemctl start cpp-phase1-5-fingerprint-worker.service
sleep 2
systemctl start cpp-phase1-5-logtail.service

mark "07-worker-started"

echo "=== Phase 1.5 Fingerprinting boot complete $(date -u) ==="
aws s3 cp /var/log/cpp-boot-phase1-5.log s3://jerard-activefounder/build-state/cpp-phase1-5-boot.log --region us-east-1 || true
echo "complete" | aws s3 cp - s3://jerard-activefounder/build-state/cpp-phase1-5-boot-DONE --region us-east-1
