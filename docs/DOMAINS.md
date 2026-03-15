# ContextPulse — Domain Management

**Registrar:** Cloudflare (david@jerardventures.com)
**Account ID:** 520086e741f5447328d166067320183b
**Created:** 2026-03-15

---

## Domains

| Domain | TLD | Purpose | Status | Annual Cost |
|--------|-----|---------|--------|-------------|
| contextpulse.ai | .ai | Primary brand domain | REGISTERED 2026-03-15 | $80.00/yr (expires 2028-03-15, 2-yr) |
| contextpulse.dev | .dev | Developer-facing, docs | REGISTERED 2026-03-15 | $12.00/yr (expires 2027-03-15) |
| contextpulse.io | .io | Credibility / redirect | REGISTERED 2026-03-15 | $50.00/yr (expires 2027-03-15) |
| context-pulse.com | .com | Hyphenated .com fallback | REGISTERED 2026-03-15 | $10.46/yr (expires 2027-03-15) |
| contextpulse.com | .com | Ideal but squatted | GoDaddy squatter — negotiate later |

## Registration Process (Cloudflare Dashboard)

Cloudflare does **not** support domain registration via API (Enterprise-only).
Registration must be done through the dashboard.

### Steps
1. Go to https://dash.cloudflare.com → **Domain Registration** → **Register Domains**
2. Search `contextpulse`
3. Select `.ai`, `.dev`, `.io` TLDs
4. Fill in registrant contact info (david@jerardventures.com)
5. Add payment method and complete checkout
6. Domains appear under **Domain Registration** → **Manage Domains**

### After Registration
- Enable **DNSSEC** on all three domains (free, one-click in Cloudflare)
- Enable **Domain Lock** (prevents unauthorized transfers)
- Verify **WHOIS privacy** is enabled (free on Cloudflare for supported TLDs)
- Set **auto-renew** on all domains

## API Access (Post-Registration)

Cloudflare API can manage domains after they're registered:

```bash
# List registered domains
curl "https://api.cloudflare.com/client/v4/accounts/520086e741f5447328d166067320183b/registrar/domains" \
  -H "Authorization: Bearer <USER_API_TOKEN>"

# Get single domain details
curl "https://api.cloudflare.com/client/v4/accounts/520086e741f5447328d166067320183b/registrar/domains/contextpulse.ai" \
  -H "Authorization: Bearer <USER_API_TOKEN>"

# Update domain settings (requires Edit scope)
curl -X PUT "https://api.cloudflare.com/client/v4/accounts/520086e741f5447328d166067320183b/registrar/domains/contextpulse.ai" \
  -H "Authorization: Bearer <USER_API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"auto_renew": true, "locked": true}'
```

### API Token Setup

**Location:** https://dash.cloudflare.com/profile/api-tokens (User API Tokens, NOT Account API Tokens)

| Token | Scope | Permission | Purpose |
|-------|-------|------------|---------|
| domain-registration | Account → Registrar/Domains | Read | Query domain status, check availability |
| domain-management | Account → Registrar/Domains | Edit | Update settings, auto-renew, lock/unlock |

**Important:** Account-level API tokens (Manage Account page) do NOT work with the Registrar API. Must use User-level API tokens (My Profile page).

## DNS Strategy

| Domain | DNS Points To | Notes |
|--------|--------------|-------|
| contextpulse.ai | Landing page (when ready) | Primary brand |
| contextpulse.dev | Docs site (when ready) | HSTS preloaded (always HTTPS) |
| contextpulse.io | Redirect → contextpulse.ai | Credibility alias |

## Other Brand Assets

| Asset | Handle/URL | Status |
|-------|-----------|--------|
| GitHub org | github.com/contextpulse | Available (404 confirmed 2026-03-15) |
| Twitter/X | @contextpulse | Unchecked — verify manually |
| npm org | @contextpulse | Unchecked |
| PyPI | contextpulse-* | Package names available (project uses contextpulse_screen etc.) |

## Cost Summary

| Item | Annual Cost |
|------|------------|
| contextpulse.ai | $80.00/yr ($160 first year — 2-yr minimum) |
| contextpulse.dev | $12.00/yr |
| contextpulse.io | $50.00/yr |
| context-pulse.com | $10.46/yr |
| **Total** | **$152.46/yr ($232.46 first year)** |

## Comparison: Google Cloud Domains vs Cloudflare

| Factor | Google Cloud Domains | Cloudflare |
|--------|---------------------|------------|
| .ai support | **No** (UNSUPPORTED) | Yes |
| .dev pricing | $12/yr | ~$12/yr (wholesale) |
| .io pricing | $60/yr | ~$25–60/yr |
| WHOIS privacy | Free (.dev), none (.io) | Free on all supported TLDs |
| DNSSEC | Free | Free |
| API registration | Yes (gcloud CLI) | No (Enterprise only) |
| API management | Yes | Yes |
| Why Cloudflare | Only option that supports all 3 TLDs in one registrar |
