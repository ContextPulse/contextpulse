# ContextPulse — Domain Management

**Registrar:** Cloudflare (david@jerardventures.com)
**Account ID:** 520086e741f5447328d166067320183b
**Created:** 2026-03-15

---

## Platform Domains

| Domain | TLD | Purpose | Status | Annual Cost |
|--------|-----|---------|--------|-------------|
| contextpulse.ai | .ai | Primary brand domain | REGISTERED 2026-03-15 | $80.00/yr (2-yr minimum) |
| contextpulse.dev | .dev | Developer-facing, docs | REGISTERED 2026-03-15 | $12.00/yr |
| contextpulse.io | .io | Credibility / redirect | REGISTERED 2026-03-15 | $50.00/yr |
| context-pulse.com | .com | Hyphenated .com fallback | REGISTERED 2026-03-15 | $10.46/yr |
| contextpulse.com | .com | Ideal but squatted | GoDaddy squatter — negotiate later |

## Sub-Product Domains

| Product | Domain | TLD | Status | Annual Cost |
|---------|--------|-----|--------|-------------|
| **Sight** | contextsight.ai | .ai | REGISTERED 2026-03-22 | ~$80/yr |
| **Sight** | context-sight.com | .com | REGISTERED 2026-03-22 | ~$10/yr |
| **Touch** | contexttouch.ai | .ai | REGISTERED 2026-03-22 | ~$80/yr |
| **Touch** | contexttouch.com | .com | REGISTERED 2026-03-22 | ~$10/yr |
| **Ear** | contextear.ai | .ai | REGISTERED 2026-03-22 | ~$80/yr |
| **Ear** | contextear.com | .com | REGISTERED 2026-03-22 | ~$10/yr |
| **Memory** | contextmemory.dev | .dev | REGISTERED 2026-03-22 | ~$12/yr |
| **Heart** | contextheart.ai | .ai | REGISTERED 2026-03-22 | ~$80/yr |
| **Heart** | contextheart.com | .com | REGISTERED 2026-03-22 | ~$10/yr |
| **People** | contextpeople.ai | .ai | REGISTERED 2026-03-22 | ~$80/yr |
| **People** | contextpeople.com | .com | REGISTERED 2026-03-22 | ~$10/yr |

## Typo Domain (to cancel/expire)

| Domain | Note |
|--------|------|
| contexheart.com | Missing 't' — registered by accident 2026-03-22. Let expire. |

## Cost Summary

| Category | Annual Cost |
|----------|------------|
| Platform (4 domains) | ~$152/yr |
| Sub-products (11 domains) | ~$462/yr |
| **Total** | **~$614/yr** |

## DNS Strategy

| Domain | DNS Points To | Notes |
|--------|--------------|-------|
| contextpulse.ai | Landing page (after patent filed) | Primary brand |
| contextpulse.dev | Docs site (when ready) | HSTS preloaded (always HTTPS) |
| contextpulse.io | Redirect → contextpulse.ai | Credibility alias |
| context-pulse.com | Redirect → contextpulse.ai | .com fallback |
| contextsight.ai | Sight product page (when ready) | First product |
| All others | Parked / redirect → contextpulse.ai | Until products ship |

## Registration Process (Cloudflare Dashboard)

Cloudflare does **not** support domain registration via API (Enterprise-only).
Registration must be done through the dashboard.

### Steps
1. Go to https://dash.cloudflare.com → **Domain Registration** → **Register Domains**
2. Search domain name
3. Select TLD
4. Fill in registrant contact info (david@jerardventures.com)
5. Add payment method and complete checkout
6. Domains appear under **Domain Registration** → **Manage Domains**

### After Registration
- Enable **DNSSEC** on all domains (free, one-click in Cloudflare)
- Enable **Domain Lock** (prevents unauthorized transfers)
- Verify **WHOIS privacy** is enabled (free on Cloudflare for supported TLDs)
- Set **auto-renew** on all domains

## Other Brand Assets

| Asset | Handle/URL | Status |
|-------|-----------|--------|
| GitHub org | github.com/contextpulse | Available (404 confirmed 2026-03-15) |
| Twitter/X | @contextpulse | Unchecked — verify manually |
| npm org | @contextpulse | Unchecked |
| PyPI | contextpulse-* | Package names available |
