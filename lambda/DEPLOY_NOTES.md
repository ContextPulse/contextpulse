# ContextPulse Lambda Deployment Notes

## Architecture

```
Gumroad Sale
    |
    v (POST /webhook)
API Gateway (REGIONAL, throttled 5 req/s)
    |
    v
Lambda: contextpulse-license-webhook
    ├── Validates Gumroad HMAC-SHA256 signature
    ├── Extracts buyer email, tier, price
    ├── Generates Ed25519-signed license key
    ├── Stores in DynamoDB (contextpulse-licenses)
    └── Sends license via SES (license@contextpulse.ai)
```

## Prerequisites
- Your AWS account, region us-east-1
- SES domain verification for contextpulse.ai (+ DKIM records in Cloudflare)
- Ed25519 keypair generated and private key in SSM Parameter Store
- Gumroad product listing for Memory Starter ($29) and Memory Pro ($49)
- SAM CLI installed (`pip install aws-sam-cli`)

## Infrastructure (SAM template.yaml)

| Resource | Type | Purpose |
|----------|------|---------|
| `contextpulse-licenses` | DynamoDB table | License records (email PK, PITR enabled) |
| `contextpulse-license-webhook` | Lambda function | Webhook handler (Python 3.12, 128MB, 30s) |
| `WebhookApi` | API Gateway | POST /webhook endpoint (5 req/s throttle) |

## Environment Variables

| Variable | Source | Description |
|----------|--------|-------------|
| `PRIVATE_KEY_HEX` | SSM `/contextpulse/license-private-key` | Ed25519 private key (hex, 64 chars) |
| `SENDER_EMAIL` | Hardcoded | `license@contextpulse.ai` |
| `GUMROAD_PRODUCT_ID` | Gumroad dashboard | Product ID for validation (optional) |
| `GUMROAD_WEBHOOK_SECRET` | SSM `/contextpulse/gumroad-webhook-secret` | HMAC signing secret (optional) |

## Key Pair

- **Public key** (embedded in `packages/core/src/contextpulse_core/license.py`):
  `6fd4deee73d32f2006f24331b552bc1f4b34f5bbda03e86ad1175bd3972c95ec`
- **Private key**: Stored in AWS SSM Parameter Store at `/contextpulse/license-private-key`
  NEVER commit the private key to this repo. Retrieve it via:
  `aws ssm get-parameter --name /contextpulse/license-private-key --with-decryption`

## IAM Permissions (minimal)
- `ses:SendEmail` (scoped to contextpulse.ai identity)
- `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:GetItem` (scoped to contextpulse-licenses table)

## Deploy

```bash
# One-command deploy (fetches secrets from SSM automatically)
cd lambda/
chmod +x deploy.sh
./deploy.sh              # prod
./deploy.sh staging      # staging
```

## Post-Deploy Steps
1. `aws ses verify-domain-identity --domain contextpulse.ai`
2. Add DKIM records to Cloudflare DNS
3. Configure Gumroad product webhook URL to the output `WebhookUrl`
4. Test with a Gumroad test purchase

## License Key Format

```
base64url(json_payload) + "." + base64url(ed25519_signature)
```

Payload:
```json
{
  "email": "buyer@example.com",
  "tier": "pro",
  "features": ["search_all_events", "get_event_timeline"],
  "ts": 1711234567,
  "exp": 1742770567
}
```

Desktop app verifies signature against embedded public key, checks expiration,
and reads `features` list to gate specific MCP tools.

## Webhook Events Handled
- **Sale**: Generates license, stores in DynamoDB, emails buyer
- **Recurring charge**: Generates fresh license with new expiration
- **Refund**: Marks license as `revoked` in DynamoDB
