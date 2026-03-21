# ContextPulse Lambda Deployment Notes

## Prerequisites
- SES domain verification for contextpulse.ai
- DynamoDB table: `contextpulse-usage` (partition key: `email`, type: String)
- Gumroad product listing for Memory Starter ($29) and Memory Pro ($49)

## Lambda Function: contextpulse-license-webhook

**Runtime:** Python 3.12
**Handler:** license_webhook.lambda_handler
**Timeout:** 30s
**Memory:** 128 MB

### Environment Variables
```
PRIVATE_KEY_HEX=<retrieve from AWS SSM Parameter Store: /contextpulse/license-private-key>
SENDER_EMAIL=license@contextpulse.ai
GUMROAD_PRODUCT_ID=<from Gumroad after creating product>
```

### IAM Permissions
- ses:SendEmail (resource: contextpulse.ai identity)
- dynamodb:PutItem (resource: contextpulse-usage table)

### Trigger
- API Gateway HTTP API (or Function URL)
- POST endpoint → Gumroad webhook URL

### Dependencies (Lambda layer or zip)
- cryptography
- boto3 (included in Lambda runtime)

## Key Pair
- **Public key** (embedded in `packages/core/src/contextpulse_core/license.py`):
  `6fd4deee73d32f2006f24331b552bc1f4b34f5bbda03e86ad1175bd3972c95ec`
- **Private key**: Stored in AWS SSM Parameter Store at `/contextpulse/license-private-key`
  NEVER commit the private key to this repo. Retrieve it via:
  `aws ssm get-parameter --name /contextpulse/license-private-key --with-decryption`

## Deployment Steps
1. `aws ses verify-domain-identity --domain contextpulse.ai`
2. Add DKIM records to Cloudflare DNS
3. Create DynamoDB table: `aws dynamodb create-table --table-name contextpulse-usage --attribute-definitions AttributeName=email,AttributeType=S --key-schema AttributeName=email,KeyType=HASH --billing-mode PAY_PER_REQUEST`
4. Create Lambda function with license_webhook.py
5. Add API Gateway trigger
6. Set env vars
7. Configure Gumroad webhook URL → API Gateway endpoint
