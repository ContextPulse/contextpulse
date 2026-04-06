#!/usr/bin/env bash
# Deploy the ContextPulse license webhook Lambda (zip-based, no SAM).
#
# NOTE: The preferred deployment method is SAM (lambda/deploy.sh + template.yaml).
# This script is for manual/ad-hoc deployments or environments without SAM CLI.
#
# Prerequisites:
#   - AWS CLI configured
#   - Lambda function already created: contextpulse-license-webhook
#   - Private key stored in SSM: /contextpulse/license-private-key
#
# Usage:
#   ./deploy.sh              # Build and deploy
#   ./deploy.sh --dry-run    # Build zip only, skip upload

set -euo pipefail

FUNCTION_NAME="contextpulse-license-webhook"
REGION="us-east-1"
DRY_RUN="${1:-}"

echo "=== ContextPulse License Webhook Deploy (zip) ==="

# Clean previous build
rm -rf package/ license_webhook.zip

# Install Python dependencies into package/
echo "Installing dependencies..."
pip install -r requirements.txt -t package/ --quiet --upgrade

# Copy handler into package
cp handler.py package/

# Zip everything
echo "Building zip..."
cd package
zip -r ../license_webhook.zip . -q
cd ..

echo "Built: license_webhook.zip ($(du -sh license_webhook.zip | cut -f1))"

if [ "${DRY_RUN}" = "--dry-run" ]; then
    echo "Dry run -- skipping upload"
    exit 0
fi

# Upload to Lambda
echo "Deploying to Lambda: ${FUNCTION_NAME} (${REGION})"
aws lambda update-function-code \
    --function-name "${FUNCTION_NAME}" \
    --zip-file fileb://license_webhook.zip \
    --region "${REGION}"

# Update environment variables from SSM
echo "Updating Lambda environment variables..."
PRIVATE_KEY_HEX=$(aws ssm get-parameter \
    --name "/contextpulse/license-private-key" \
    --with-decryption \
    --query 'Parameter.Value' \
    --output text \
    --region "${REGION}")

WEBHOOK_SECRET=""
if aws ssm get-parameter \
    --name "/contextpulse/gumroad-webhook-secret" \
    --region "${REGION}" &>/dev/null; then
    WEBHOOK_SECRET=$(aws ssm get-parameter \
        --name "/contextpulse/gumroad-webhook-secret" \
        --with-decryption \
        --query 'Parameter.Value' \
        --output text \
        --region "${REGION}")
fi

aws lambda update-function-configuration \
    --function-name "${FUNCTION_NAME}" \
    --region "${REGION}" \
    --environment "Variables={
        PRIVATE_KEY_HEX=${PRIVATE_KEY_HEX},
        SENDER_EMAIL=license@contextpulse.ai,
        GUMROAD_WEBHOOK_SECRET=${WEBHOOK_SECRET}
    }"

echo "=== Deploy complete ==="
echo ""
echo "NOTE: For production deployments, prefer SAM:"
echo "  cd lambda/ && ./deploy.sh"
