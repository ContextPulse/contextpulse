# ContextPulse — Regulatory Compliance Plan
**March 2026 | Jerard Ventures LLC | CONFIDENTIAL**

---

## Overview

ContextPulse's on-device, privacy-first architecture provides a structural head start on compliance vs. cloud-dependent competitors. All data remains on the user's machine. No screenshots, OCR text, clipboard content, or activity metadata ever leaves the device. This is not a policy commitment — it is an architectural constraint enforced at the code level.

This document covers: GDPR, CCPA, SOC 2, Section 508 / ADA, HIPAA (roadmap), and the privacy-by-design decisions that underpin all of them.

---

## 1. GDPR (EU General Data Protection Regulation)

### Why ContextPulse Has a Natural Advantage

GDPR's core requirement is that personal data be processed lawfully, stored securely, and made available for deletion on request. ContextPulse's architecture satisfies most GDPR obligations structurally rather than through policy:

- **No data transmission to ContextPulse servers.** All captured data is stored locally in `C:\Users\<user>\AppData\Local\ContextPulse\` (Windows). No EU data residency requirement applies because data never leaves the device.
- **Data minimization (Article 5(1)(c)):** Change-detection filtering reduces stored frames by 40–60%. Content-adaptive storage saves only OCR text when visual context is not needed. The system captures only what changes, not everything.
- **Purpose limitation (Article 5(1)(b)):** Captures are used exclusively for context delivery to authorized MCP clients on the same machine. No secondary processing.
- **Pre-storage redaction (Article 25 — Data Protection by Design):** 10+ sensitive data categories (API keys, JWTs, credit card numbers, SSNs, PEM certificates, connection strings, bearer tokens) are redacted before they reach the SQLite database. This is privacy-by-design enforced before storage — not a post-hoc filter.

### GDPR Compliance Actions

| Requirement | Status | Action Required | Timeline |
|-------------|--------|-----------------|----------|
| Privacy Policy (Article 13) | Not yet published | Write and publish at contextpulse.ai/privacy before public launch | Q2 2026 |
| Data Subject Rights (Article 15–22) | Partial | "Wipe all data" option already in UI. Add export-all function (Article 20 portability) | Q3 2026 |
| Lawful basis (Article 6) | Covered | Legitimate interests (performance of software contract) + consent captured at install | Q2 2026 |
| Data Processor agreements | N/A (no cloud) | No DPA needed — no third-party data processors for on-device data | Perpetual |
| Breach notification (Article 33) | N/A | If a local device is compromised, that is the user's scope, not ContextPulse's. Document this limitation in Privacy Policy | Q2 2026 |
| EU representative (Article 27) | Not required until €10M+ EU revenue or systematic monitoring | Not required at current scale | 2028+ |
| Cookie consent | Minimal | Only needed for the website (contextpulse.ai), not the product | Q2 2026 |

**Key GDPR selling point:** "ContextPulse is the only screen-context tool that is GDPR-native by architecture, not by policy. Your data cannot reach our servers because we have no servers that receive it."

---

## 2. CCPA (California Consumer Privacy Act)

### Applicability Threshold

CCPA applies to for-profit businesses that: (a) have $25M+ in annual gross revenue, OR (b) buy/sell/receive/share personal information of 100K+ California residents annually, OR (c) derive 50%+ of annual revenue from selling personal information.

**Current status:** ContextPulse does not meet any CCPA threshold as of 2026. However, proactive alignment costs nothing and strengthens the enterprise sales story.

### CCPA-Ready Architecture (Current)

- **No sale of personal information:** ContextPulse's revenue model (one-time license, SaaS subscription) does not involve selling user data to third parties.
- **Right to know / Right to delete:** The local SQLite database is under full user control. "Wipe all data" clears the database entirely.
- **Sensitive personal information:** Screenshots may incidentally capture SSNs, financial data, health information. Pre-storage redaction addresses the highest-risk categories. This also addresses CPRA (California Privacy Rights Act) "sensitive PI" requirements.

### CCPA Actions

| Action | Timeline |
|--------|----------|
| Add CCPA disclosure to Privacy Policy (even before threshold applies) | Q2 2026 |
| "Do Not Sell My Personal Information" link on website (best practice, not required below threshold) | Q3 2026 |
| Formal CCPA compliance review at $5M ARR | 2029 |

---

## 3. SOC 2 Roadmap

SOC 2 Type II certification is required for enterprise contracts at Fortune 500 companies, government contractors, financial services, and healthcare. It is the de facto requirement for any B2B SaaS deal above $50K/year.

### What SOC 2 Covers (Trust Service Criteria)

- **Security** (required): Information and systems are protected against unauthorized access
- **Availability** (optional): System is available for operation as committed
- **Confidentiality** (optional): Information designated as confidential is protected
- **Processing Integrity** (optional): System processing is complete, valid, accurate, timely
- **Privacy** (optional): Personal information is collected, used, retained, disclosed per the privacy notice

ContextPulse's initial SOC 2 scope: **Security** (required) + **Confidentiality** (supports enterprise deals). Availability and Privacy are deferred to Type II.

### SOC 2 Roadmap

#### Phase 1: SOC 2 Type I Self-Assessment — Q4 2026 ($3K–$8K)

Tools: **Vanta** ($3,600/year) or **Drata** ($5,000/year) automate evidence collection. For a solo-founder software company, Vanta is the standard path.

| Control | Current Status | Gap |
|---------|---------------|-----|
| Access control (CC6.1) | Local SQLite, no remote access | Document access control policy; add password/auth to admin functions |
| Encryption at rest (CC6.7) | SQLite unencrypted by default | Add SQLite encryption (SQLCipher) before Type I |
| Encryption in transit (CC6.7) | No data in transit (on-device) | N/A — document this as architectural control |
| Change management (CC8.1) | Git version control, tests | Document change management policy |
| Incident response (CC7.3) | None | Write incident response plan (1-page, realistic) |
| Vendor management (CC9.2) | Minimal vendors | Document AWS Lambda + Gumroad as vendors |
| Background checks (CC1.4) | Solo founder | N/A at current scale; document for contractor onboarding |

**Estimated cost for Phase 1:**
- Vanta subscription: $3,600/year
- External auditor (readiness review): $2,000–$4,000
- SQLCipher integration (development time): ~10 hours
- Policy documentation (1-2 days): Founder time
- **Total: $6K–$9K**

#### Phase 2: SOC 2 Type I Report — Q2 2027 ($8K–$15K)

Formal Type I audit by a licensed CPA firm. Type I confirms controls are designed correctly (point-in-time). This is sufficient for most mid-market enterprise deals.

Recommended audit firms for early-stage SaaS:
- Prescient Assurance (SOC 2 specialist, ~$8K for Type I)
- Schellman (premium, ~$15K for Type I)
- A-LIGN (mid-market, ~$10K for Type I)

#### Phase 3: SOC 2 Type II — Q4 2027 ($15K–$25K)

Type II covers a period of at least 6 months of control operation. Required for federal contracts, healthcare, and Fortune 500 deals. Type II is the de facto standard for any $50K+ enterprise contract.

### SOC 2 Timeline Summary

| Milestone | Target | Cost |
|-----------|--------|------|
| Vanta setup + gap analysis | Q4 2026 | $3,600 |
| SQLCipher (encryption at rest) | Q4 2026 | Dev time |
| Policy documentation | Q4 2026 | Founder time |
| SOC 2 Type I audit | Q2 2027 | $8K–$15K |
| SOC 2 Type II audit | Q4 2027 | $15K–$25K |
| HIPAA BAA capability (healthcare channel) | Q2 2028 | +$5K–$10K to SOC 2 scope |

---

## 4. Privacy-by-Design Architecture (IEEE/ISO 29101 Alignment)

Privacy-by-design is not a compliance checkbox — it is ContextPulse's core competitive differentiator. This section documents the architectural decisions that make compliance natural rather than bolted-on.

### Seven Principles (Cavoukian Framework) — ContextPulse Implementation

| Principle | ContextPulse Implementation |
|-----------|----------------------------|
| **1. Proactive, not reactive** | Pre-storage redaction fires before any data writes to SQLite. No sensitive data can be stored accidentally. |
| **2. Privacy as the default** | Auto-pause on session lock (Win32 WTS_SESSION_LOCK). Window title blocklist active by default (banking, medical, email apps excluded from capture). |
| **3. Privacy embedded into design** | No cloud upload path exists in the codebase. The MCP server is local socket only (`localhost`). No telemetry endpoints. |
| **4. Full functionality — positive-sum** | Privacy-first AND full context delivery. Not a trade-off — the on-device architecture enables both simultaneously. |
| **5. End-to-end security** | SQLCipher encryption at rest (Q4 2026 roadmap). Local-only processing. No data in transit. |
| **6. Visibility and transparency** | System tray icon shows capture state (active/paused). Activity log accessible to user. "Wipe all data" is a single command. |
| **7. Respect for user privacy** | No account required. No license phone-home beyond a single activation check. User can inspect everything stored about them via `search_recent`. |

### Data Flow Diagram (Privacy-by-Design Documentation)

```
Screen pixels → [Change detection] → [OCR pipeline] → [Pre-storage redaction]
                                                            ↓ (if sensitive pattern found)
                                                      [Redact before write]
                                                            ↓
                                                   [SQLite (local disk only)]
                                                            ↓
                                              [MCP server (localhost only)]
                                                            ↓
                                          [AI agent on same machine]

NEVER: → Internet → ContextPulse servers → Third parties → Acquirer data
```

### Sensitive Data Redaction Categories (Pre-Storage, Current)

| Category | Example Pattern | Redaction Method |
|----------|-----------------|-----------------|
| API Keys | `sk-...`, `AKIA...`, `ghp_...` | Regex replace with `[REDACTED-API-KEY]` |
| JWT Tokens | `eyJ...` base64 header | Pattern match + replace |
| Credit Card Numbers | 16-digit Luhn-valid patterns | Regex + Luhn check |
| Social Security Numbers | `XXX-XX-XXXX` format | Regex replace |
| PEM Certificates | `-----BEGIN CERTIFICATE-----` blocks | Pattern match + replace |
| Database Connection Strings | `postgres://`, `mongodb+srv://` | Regex replace |
| Bearer Tokens | `Authorization: Bearer ...` | Header pattern match |
| AWS Secret Keys | `aws_secret_access_key = ...` | Pattern match |
| Private Keys | `-----BEGIN RSA PRIVATE KEY-----` | Pattern match |
| Passwords in URLs | `https://user:password@...` | URL credential strip |

---

## 5. Section 508 / ADA Compliance

### Why This Matters for ContextPulse

Section 508 of the Rehabilitation Act requires U.S. federal agencies and federally funded organizations to use accessible electronic and information technology. ContextPulse's USPTO Class 10 trademark filing (medical devices / assistive technology) creates a direct path to the federal procurement market — but only if ContextPulse can demonstrate 508 compliance.

### Section 508 Roadmap

| Milestone | Target | Cost | Unlock |
|-----------|--------|------|--------|
| VPAT (Voluntary Product Accessibility Template) for Sight + Voice | Q2 2027 | $3K–$8K (consultant) | Opens federal RFP eligibility |
| System tray and UI keyboard navigation | Q4 2026 | Dev time | AT user usability |
| Screen reader compatibility (NVDA, JAWS) for settings UI | Q1 2027 | Dev time | Blind user testing |
| GSA Schedule Application (IT Schedule 70 / Schedule 70) | Q1 2028 | $5K–$15K (GSA consultant) | Direct federal procurement |

**Note on accessibility as a moat:** No AI context tool has filed USPTO Class 10 or has a VPAT in progress. ContextPulse's accessibility architecture creates a government procurement channel that would take a competitor 18–24 months to replicate even if they started today.

---

## 6. HIPAA (Healthcare — Roadmap)

### When HIPAA Applies

HIPAA applies when ContextPulse captures Protected Health Information (PHI) in a clinical context — e.g., a physician using ContextPulse while reviewing patient records in Epic/Cerner. At current scale, HIPAA is not triggered (no covered entity customers, no PHI processing agreements).

### HIPAA Prerequisite Checklist

- [ ] SOC 2 Type II completed (provides ~70% of HIPAA technical safeguards)
- [ ] Business Associate Agreement (BAA) template drafted
- [ ] PHI in pre-storage redaction patterns (patient names, MRNs, DOBs added to redaction rules)
- [ ] Audit logging enabled for all MCP tool calls (who accessed what context, when)
- [ ] Workforce training documentation (HIPAA Security Rule requirement)

**Target:** HIPAA BAA capability by Q2 2028, coinciding with first healthcare enterprise pilot. Otter.ai achieved HIPAA compliance in July 2025 — this is a reachable milestone for a meeting-context competitor, and ContextPulse's on-device architecture makes it significantly easier than cloud-dependent tools.

---

## 7. Enterprise Sales Compliance Checklist

What a security-conscious enterprise buyer will ask during procurement. Current readiness:

| Question | Current Status | Gap | Target Date |
|----------|---------------|-----|-------------|
| "Do you have SOC 2?" | Not yet | Type I in progress | Q2 2027 |
| "Is our data stored in your cloud?" | **No — on-device only** | N/A | Always |
| "Do you comply with GDPR?" | **Architecture-native** | Privacy policy publication | Q2 2026 |
| "Can we wipe all data?" | **Yes — single command** | N/A | Shipped |
| "Do you have a DPA (Data Processing Agreement)?" | No | Draft template | Q3 2026 |
| "How do you handle sensitive data (PII, credentials)?" | **Pre-storage redaction, 10+ categories** | N/A | Shipped |
| "Do you support SSO (SAML/OIDC)?" | No | Enterprise tier roadmap | Q3 2028 |
| "Do you have a penetration test?" | No | External pen test | Q1 2028 |
| "Are you HIPAA compliant?" | No | Q2 2028 roadmap | Q2 2028 |
| "What is your breach notification process?" | Not documented | Write + publish incident response plan | Q4 2026 |
| "Do you have cyber liability insurance?" | No | $1M–$2M policy | Q3 2027 |

---

## Summary: Compliance as Competitive Advantage

ContextPulse's privacy-by-design architecture gives it a structural compliance advantage over every competitor:

| Compliance Area | ContextPulse | Screenpipe | MS Recall | Granola | Otter.ai |
|----------------|:---:|:---:|:---:|:---:|:---:|
| GDPR (architecture-native) | ✓ | ✓ | ~ | ✗ | ✗ |
| No cloud data transmission | ✓ | ✓ | ✓† | ✗ | ✗ |
| Pre-storage PII redaction | **✓ (10+ categories)** | ✗ | ✗ | ✗ | ✗ |
| SOC 2 Type II | Roadmap Q4 2027 | None | N/A (Microsoft) | None | ✓ |
| HIPAA | Roadmap Q2 2028 | None | None | None | ✓ (July 2025) |
| Section 508 / VPAT | Roadmap Q2 2027 | None | ~ (hardware-gated) | None | ~ (captioning only) |
| GSA Schedule | Roadmap Q1 2028 | None | None | None | None |

† Microsoft Recall: on-device within Copilot+ PCs but still transmits Windows telemetry; EEA rollout completed late 2025.

**The compliance pitch in one sentence:** "ContextPulse cannot leak your data to our cloud because our cloud cannot receive it — and the sensitive data that would be highest-risk is redacted before it even hits your local disk."

---

*Last updated: March 2026. Review quarterly; update before each enterprise pilot RFP.*
