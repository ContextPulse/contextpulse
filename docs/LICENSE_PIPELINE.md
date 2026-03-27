# ContextPulse License Delivery Pipeline

## Architecture

```
 Gumroad Checkout                  AWS (us-east-1, account 397348547231)
 +--------------+                  +-------------------------------------------+
 | Customer     |  POST /webhook   | API Gateway (REGIONAL, 5 req/s throttle)  |
 | buys Memory  | ---------------> | https://{api-id}.execute-api.../{stage}/   |
 | Starter/Pro  |                  |   webhook                                 |
 +--------------+                  +--------------------+-----------------------+
                                                        |
                                                        v
                                   +--------------------+-----------------------+
                                   | Lambda: contextpulse-license-webhook       |
                                   | Python 3.12 | 128 MB | 30s timeout        |
                                   |                                            |
                                   | 1. Validate Gumroad HMAC-SHA256 signature  |
                                   | 2. Check idempotency (sale_id in DynamoDB) |
                                   | 3. Detect tier from variant/price          |
                                   | 4. Generate Ed25519-signed license key     |
                                   | 5. Store in DynamoDB                       |
                                   | 6. Email license via SES                  |
                                   +-----+------------------+------------------+
                                         |                  |
                      +------------------+     +------------+------------+
                      v                        v                        v
          +-----------+----------+  +----------+---------+  +-----------+--------+
          | DynamoDB             |  | SES                |  | SSM Param Store    |
          | contextpulse-        |  | From:              |  | /contextpulse/     |
          | licenses             |  | license@           |  |   license-private- |
          | (PAY_PER_REQUEST,   |  | contextpulse.ai    |  |   key              |
          |  PITR enabled)      |  |                    |  | /contextpulse/     |
          +----------------------+  +--------------------+  |   gumroad-webhook- |
                                                            |   secret           |
                                                            +--------------------+

 Desktop Client
 +------------------------------------------+
 | ContextPulse.exe                         |
 |                                          |
 | license.py:                              |
 |   Embedded Ed25519 public key            |
 |   Verifies signature locally (offline)   |
 |   Stores key: %APPDATA%/ContextPulse/    |
 |     license.key                          |
 |   7-day trial, 3-day grace period        |
 |                                          |
 | license_dialog.py:                       |
 |   Tray icon "Enter License Key" dialog   |
 +------------------------------------------+
```

## Full Flow: Purchase to Activation

1. **Customer purchases** Memory Starter ($29) or Memory Pro ($49) on Gumroad
2. **Gumroad sends POST** to API Gateway `/webhook` with form-encoded sale data
3. **Lambda validates** the HMAC-SHA256 webhook signature (if `GUMROAD_WEBHOOK_SECRET` is configured)
4. **Lambda checks idempotency** -- looks up the buyer's email in DynamoDB and compares `sale_id` to skip duplicate webhook deliveries
5. **Lambda detects tier** from Gumroad variant name or price
6. **Lambda generates license key**: JSON payload (email, tier, features, timestamps) signed with Ed25519 private key, encoded as `base64url(payload).base64url(signature)`
7. **Lambda stores record** in DynamoDB: email (PK), sale_id, tier, features, license_key_hash (SHA256), price, timestamps, status
8. **Lambda sends email** via SES with branded HTML containing the license key and activation instructions
9. **Customer receives email**, copies the license key
10. **Customer right-clicks** ContextPulse tray icon, selects "Enter License Key"
11. **Desktop verifies** the key locally using the embedded Ed25519 public key -- no internet required
12. **Key is saved** to `%APPDATA%\ContextPulse\license.key`
13. **Pro features unlocked**: `search_all_events`, `get_event_timeline`

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

- Signature verified against embedded public key (no server call needed)
- `exp` is Unix timestamp, 365 days from purchase
- 3-day grace period after expiry before hard feature block

## Webhook Events Handled

| Gumroad Event | Lambda Behavior |
|---------------|-----------------|
| **Sale** | Generates license, stores in DynamoDB, emails buyer |
| **Recurring charge** | Generates fresh license with new expiration |
| **Refund** | Marks license as `revoked` in DynamoDB |
| **Duplicate webhook** | Detected via `sale_id` match, returns 200 OK without re-processing |

## Deployment Steps (Go-Live Checklist)

### Prerequisites
- AWS CLI configured for account `397348547231`
- AWS SAM CLI installed (`pip install aws-sam-cli`)
- Ed25519 keypair generated (public key is already embedded in `license.py`)

### Step 1: Store secrets in SSM Parameter Store

```powershell
# Private key (NEVER commit this to the repo)
aws ssm put-parameter `
    --name "/contextpulse/license-private-key" `
    --value "YOUR_PRIVATE_KEY_HEX" `
    --type SecureString `
    --region us-east-1

# Gumroad webhook signing secret (from Gumroad dashboard)
aws ssm put-parameter `
    --name "/contextpulse/gumroad-webhook-secret" `
    --value "YOUR_GUMROAD_SECRET" `
    --type SecureString `
    --region us-east-1
```

### Step 2: Verify SES domain

```powershell
aws ses verify-domain-identity --domain contextpulse.ai --region us-east-1
```

This returns DKIM tokens. Add these as CNAME records in Cloudflare DNS:
- `_amazonses.contextpulse.ai` -> verification token
- Three DKIM CNAME records

Also request production SES access (out of sandbox) to send to any email address.

### Step 3: Deploy the Lambda stack

```bash
cd lambda/
chmod +x deploy.sh
./deploy.sh              # deploys to prod
# ./deploy.sh staging    # deploys to staging
```

The script:
1. Fetches private key from SSM
2. Creates S3 bucket for SAM artifacts if needed
3. Installs Python dependencies
4. Runs `sam build` and `sam deploy`
5. Prints the webhook URL

### Step 4: Configure Gumroad

1. Go to your Gumroad product settings
2. Under "Webhooks" or "Ping", add the webhook URL from the deploy output
3. Set the webhook URL for both sale and refund events
4. Note the Gumroad product ID and optionally set `GUMROAD_PRODUCT_ID` parameter

### Step 5: Test end-to-end

1. Make a test purchase on Gumroad
2. Check CloudWatch logs: `aws logs tail /aws/lambda/contextpulse-license-webhook --follow`
3. Verify DynamoDB record: `aws dynamodb get-item --table-name contextpulse-licenses --key '{"email":{"S":"your@email.com"}}'`
4. Check that the license email arrived
5. Paste the key into ContextPulse desktop -- verify it activates

## Monitoring

### CloudWatch Logs
```powershell
# Tail Lambda logs in real-time
aws logs tail /aws/lambda/contextpulse-license-webhook --follow --region us-east-1

# Search for errors in the last 24h
aws logs filter-log-events `
    --log-group-name /aws/lambda/contextpulse-license-webhook `
    --filter-pattern "ERROR" `
    --start-time (([DateTimeOffset]::UtcNow.AddDays(-1)).ToUnixTimeMilliseconds()) `
    --region us-east-1
```

### DynamoDB
```powershell
# Count total licenses
aws dynamodb scan --table-name contextpulse-licenses --select COUNT --region us-east-1

# Check a specific license
aws dynamodb get-item --table-name contextpulse-licenses `
    --key '{"email":{"S":"customer@example.com"}}' --region us-east-1
```

### SES Sending Stats
```powershell
aws ses get-send-statistics --region us-east-1
```

### API Gateway Metrics
- Check the API Gateway console for 4xx/5xx error rates
- Throttling is set to 5 req/s burst 10

## Troubleshooting

### Customer did not receive license email
1. Check CloudWatch logs for the sale timestamp -- search for customer email
2. If Lambda succeeded but email failed, the log will say "License stored but email delivery failed"
3. Look up the license in DynamoDB -- if the record exists, the license was generated
4. Re-send manually: retrieve the `license_key_hash` from DynamoDB (note: the actual key is NOT stored, only the hash). You will need to generate a new key for the customer.
5. Check SES sending quota and bounce/complaint rates

### Invalid license key on desktop
1. Verify the key was not truncated during copy-paste (keys are long base64 strings)
2. Check that the desktop app's embedded public key matches the Lambda's private key pair
3. Public key in `license.py`: `6fd4deee73d32f2006f24331b552bc1f4b34f5bbda03e86ad1175bd3972c95ec`

### Webhook returns 403 (Invalid signature)
1. Verify `GUMROAD_WEBHOOK_SECRET` in SSM matches Gumroad's webhook signing secret
2. If not using webhook signing, ensure the env var is empty (validation is skipped)
3. Check that Gumroad is sending the `Gumroad-Signature` header

### Webhook returns 400 (Missing email / Missing sale_id)
1. Gumroad payload may have changed format -- check CloudWatch logs for the raw event body
2. Ensure the webhook is configured for "sale" events (not just "subscription")

### Duplicate license generation
- The Lambda checks `sale_id` in DynamoDB before generating a new license
- If Gumroad retries a webhook, the duplicate is detected and skipped (returns 200)
- If a customer buys again (new sale_id), a new license is generated and overwrites the old DynamoDB record

### SES still in sandbox
- SES sandbox only allows sending to verified email addresses
- Request production access: AWS Console > SES > Account Dashboard > Request Production Access
- Until production access is granted, verify test recipient emails individually

### Lambda timeout
- Default timeout is 30 seconds, which is generous
- If Ed25519 key generation is slow, check the Lambda memory setting (128 MB)
- The `cryptography` library is compiled for x86_64 Linux (included in `package/` directory)

## Security Notes

- **Private key**: Lives ONLY in SSM Parameter Store and Lambda environment variables. Never committed to the repo.
- **Public key**: Embedded in desktop client (`license.py`). Safe to distribute.
- **License key hash**: DynamoDB stores SHA256 hash of the key, not the key itself. The actual key is only ever in the email.
- **Webhook validation**: HMAC-SHA256 signature check prevents forged webhook calls. Optional but strongly recommended for production.
- **IAM permissions**: Scoped to `ses:SendEmail` on `contextpulse.ai` identity and `dynamodb:PutItem/UpdateItem/GetItem` on the specific table ARN.
- **API throttling**: 5 req/s with burst of 10 prevents abuse.
- **PITR**: DynamoDB has Point-in-Time Recovery enabled for disaster recovery.
