# Compliance Mapping (Milestone 3, Part A)

SkillsOps turns its governance primitives (security scan, RBAC, runtime policy,
HMAC audit, deployment records) into **audit-ready evidence** mapped to
regulatory frameworks. Evidence collection is deterministic — no LLM, no network.

## Frameworks

Declared as YAML in `skillctl/compliance/frameworks_data/`:

| ID | Name | Notes |
|----|------|-------|
| `eu-ai-act` | EU Artificial Intelligence Act (2024/1689) | High-risk obligations, effective 2026-08-02 |
| `iso-42001` | ISO/IEC 42001 | AI management system (certifiable) |
| `nist-ai-rmf` | NIST AI RMF 1.0 | Govern / Map / Measure / Manage |

Hierarchy: **Framework → Category → Requirement → Control**. Each control names
the `evidence_types` that demonstrate compliance and whether it
`human_review_required`. Add a framework by dropping a YAML file in the data
directory — no code change.

## Evidence types → SkillsOps sources

| Evidence type | Source |
|---------------|--------|
| `security_scan` | live `skillctl eval audit` |
| `audit_log` | HMAC hash-chained audit log |
| `policy` | `policy_decision` audit events (M2) |
| `rbac` | RBAC users / role assignments / auth decisions (M1) |
| `metadata` | skill manifest frontmatter |
| `version` | registry version history |
| `deployment` | progressive deployment records (M3 Part B) |
| `manual` | human attestations |
| `otel_trace` | OpenTelemetry (not queried in this build) |

Every `EvidenceRecord` carries a SHA-256 `integrity_hash` of its content.

## Status & scoring

Per control: `COMPLIANT` (all evidence types present), `PARTIALLY_COMPLIANT`
(≥50%), `NON_COMPLIANT` (<50% / none), `PENDING_REVIEW` (human review required,
no attestation yet), `NOT_APPLICABLE` (excluded by risk level). The report score
weights compliant=1.0, partial/pending=0.5, non-compliant=0.0 over applicable
controls.

## Risk classification

`RiskClassifier` maps a skill to an EU AI Act risk level
(`UNACCEPTABLE`/`HIGH`/`LIMITED`/`MINIMAL`) using keyword analysis of the
metadata, deployment context, or an overriding human attestation. MINIMAL-risk
skills are not bound by high-risk human-review controls.

## Attestations

Controls needing human sign-off are recorded in `AttestationStore` (SQLite):
time-bounded (default 90 days), superseded on re-attestation, and invalidated
when the skill version changes.

## CLI

```bash
skillctl compliance frameworks                              # list frameworks
skillctl compliance classify ./my-skill                     # risk level
skillctl compliance report ./my-skill --framework eu-ai-act # full report (md|json)
skillctl compliance gaps ./my-skill --framework eu-ai-act   # non/partial controls
skillctl compliance attest --control art-9-2-b --skill ./my-skill \
    --statement "Residual risks reviewed and accepted per assessment v3"
```

A report exits non-zero if any control is `NON_COMPLIANT`, so it can gate CI.
