# ContextPulse DynamoDB Schema

## Overview

Two tables handle licensing and usage tracking:

| Table | Purpose |
|-------|---------|
| `contextpulse-licenses` | One record per customer email. Stores license tier, expiry, Gumroad sale_id, and delivery status. |
| `contextpulse-usage` | Per-email, per-feature, per-month usage counters. Reserved for future rate limiting of Pro API calls. |

---

## Table: `contextpulse-licenses`

Stores one license record per buyer email. On renewal, the record is overwritten with the new sale_id and expiry.

### Key Schema

| Key | Type | Role |
|-----|------|------|
| `email` | String | Partition key (PK) — buyer's email address (normalized to lowercase) |

> Note: No sort key. One active license per email. Historical records are not retained (renewals overwrite).

### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `email` | S | Buyer email (PK) |
| `sale_id` | S | Gumroad sale_id — used for idempotency checks |
| `tier` | S | License tier: `"pro"` or `"enterprise"` |
| `features` | L | List of unlocked feature names (e.g. `["memory_search", "search_all_events"]`) |
| `license_key_hash` | S | SHA-256 hash of the raw license key string (for audit; never store the key itself) |
| `price_dollars` | S | Purchase price as string (e.g. `"49.00"`) |
| `purchased_at` | S | ISO 8601 timestamp of purchase |
| `expires_at` | S | ISO 8601 timestamp of license expiry (1 year from purchase) |
| `source` | S | Payment source: `"gumroad"` or `"stripe"` |
| `is_active` | BOOL | False if refunded or manually revoked |
| `email_sent` | BOOL | True if SES delivery succeeded |
| `email_failed_at` | S | ISO 8601 timestamp if email delivery failed (for re-send queue) |

### Access Patterns

| Operation | Key |
|-----------|-----|
| Look up license by email | `GetItem(email)` |
| Idempotency check by sale_id | `GetItem(email)` then check `sale_id` attribute |
| Revoke on refund | `UpdateItem(email)` set `is_active = False` |
| Find failed email deliveries | Scan `email_sent = False` (low-volume; scan is acceptable) |

### AWS CLI — Create Table

```bash
aws dynamodb create-table \
  --table-name contextpulse-licenses \
  --billing-mode PAY_PER_REQUEST \
  --attribute-definitions AttributeName=email,AttributeType=S \
  --key-schema AttributeName=email,KeyType=HASH \
  --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true \
  --region us-east-1 \
  --tags Key=Project,Value=ContextPulse
```

---

## Table: `contextpulse-usage`

Tracks per-feature usage by email for the current month. Reserved for future
rate limiting of compute-intensive Pro features (e.g. semantic search quotas).

### Key Schema

| Key | Type | Role |
|-----|------|------|
| `email` | String | Partition key (PK) |
| `feature_month` | String | Sort key (SK) — `"{feature_name}#{YYYY-MM}"` |

Example SK: `memory_search#2026-04`

### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `email` | S | User email (PK) |
| `feature_month` | S | Sort key: feature + month (SK) |
| `count` | N | Number of calls this month |
| `limit` | N | Monthly call limit for this feature (default: 10000) |
| `last_updated` | S | ISO 8601 timestamp of last increment |

### Access Patterns

| Operation | Key |
|-----------|-----|
| Get usage for a feature this month | `GetItem(email, "memory_search#2026-04")` |
| Get all usage for a user this month | `Query(email, begins_with("2026-04"))` |
| Increment counter atomically | `UpdateItem` with `ADD count 1` |

### AWS CLI — Create Table

```bash
aws dynamodb create-table \
  --table-name contextpulse-usage \
  --billing-mode PAY_PER_REQUEST \
  --attribute-definitions \
    AttributeName=email,AttributeType=S \
    AttributeName=feature_month,AttributeType=S \
  --key-schema \
    AttributeName=email,KeyType=HASH \
    AttributeName=feature_month,KeyType=RANGE \
  --region us-east-1 \
  --tags Key=Project,Value=ContextPulse
```

---

## Notes

- Both tables use `PAY_PER_REQUEST` billing — no provisioned capacity needed at launch volume.
- `contextpulse-licenses` has PITR enabled (point-in-time recovery) to protect against accidental deletion.
- The SAM template (`lambda/template.yaml`) creates `contextpulse-licenses` automatically on deploy.
  `contextpulse-usage` must be created manually (it is not in the SAM template yet).
- Never store the raw license key string in DynamoDB — only the SHA-256 hash for audit.
