#!/usr/bin/env bash
# Deploy the ContextPulse license webhook to AWS via SAM.
#
# Prerequisites:
#   - AWS CLI configured with your AWS credentials
#   - AWS SAM CLI installed (pip install aws-sam-cli)
#   - Private key stored in SSM: /contextpulse/license-private-key
#   - SES domain verified for contextpulse.ai
#
# Usage:
#   ./deploy.sh              # Deploy to prod
#   ./deploy.sh staging      # Deploy to staging

set -euo pipefail

STAGE="${1:-prod}"
STACK_NAME="contextpulse-license-${STAGE}"
REGION="us-east-1"
S3_BUCKET="contextpulse-sam-artifacts"
SSM_KEY_PARAM="/contextpulse/license-private-key"
SSM_WEBHOOK_SECRET_PARAM="/contextpulse/gumroad-webhook-secret"

echo "=== ContextPulse License Webhook Deploy (${STAGE}) ==="

# Retrieve private key from SSM
echo "Fetching private key from SSM..."
PRIVATE_KEY_HEX=$(aws ssm get-parameter \
    --name "${SSM_KEY_PARAM}" \
    --with-decryption \
    --query 'Parameter.Value' \
    --output text \
    --region "${REGION}")

if [ -z "${PRIVATE_KEY_HEX}" ]; then
    echo "ERROR: Could not retrieve private key from SSM at ${SSM_KEY_PARAM}"
    exit 1
fi

# Retrieve webhook secret from SSM (optional)
WEBHOOK_SECRET=""
if aws ssm get-parameter --name "${SSM_WEBHOOK_SECRET_PARAM}" --region "${REGION}" &>/dev/null; then
    WEBHOOK_SECRET=$(aws ssm get-parameter \
        --name "${SSM_WEBHOOK_SECRET_PARAM}" \
        --with-decryption \
        --query 'Parameter.Value' \
        --output text \
        --region "${REGION}")
    echo "Gumroad webhook secret loaded from SSM"
else
    echo "WARNING: No Gumroad webhook secret in SSM -- signature validation disabled"
fi

# Create S3 bucket for SAM artifacts if it doesn't exist
if ! aws s3 ls "s3://${S3_BUCKET}" --region "${REGION}" &>/dev/null; then
    echo "Creating SAM artifacts bucket: ${S3_BUCKET}"
    aws s3 mb "s3://${S3_BUCKET}" --region "${REGION}"
fi

# Install Python dependencies into package/ for Lambda layer
echo "Installing dependencies..."
pip install -r requirements.txt -t package/ --quiet --upgrade 2>/dev/null || true

# Build and deploy with SAM
echo "Building SAM application..."
sam build --template-file template.yaml --region "${REGION}"

echo "Deploying to AWS..."
sam deploy \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --s3-bucket "${S3_BUCKET}" \
    --capabilities CAPABILITY_IAM \
    --parameter-overrides \
        "PrivateKeyHex=${PRIVATE_KEY_HEX}" \
        "SenderEmail=license@contextpulse.ai" \
        "GumroadWebhookSecret=${WEBHOOK_SECRET}" \
        "StageName=${STAGE}" \
    --no-confirm-changeset \
    --tags "Project=ContextPulse"

# Print the webhook URL
echo ""
echo "=== Deployment Complete ==="
WEBHOOK_URL=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --query 'Stacks[0].Outputs[?OutputKey==`WebhookUrl`].OutputValue' \
    --output text \
    --region "${REGION}")

echo "Webhook URL: ${WEBHOOK_URL}"
echo ""
echo "Next steps:"
echo "  1. Verify SES domain: aws ses verify-domain-identity --domain contextpulse.ai"
echo "  2. Add DKIM/SPF records to Cloudflare DNS"
echo "  3. Configure Gumroad product webhook URL: ${WEBHOOK_URL}"
echo "  4. Test with a Gumroad test purchase"
