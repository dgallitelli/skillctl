# Compliance Mapping Preview (Experimental)

> **Not certification or legal advice:** reports are deterministic local
> control-mapping previews. They do not establish regulatory compliance,
> certify an ISO management system, authenticate an attester, or authorize a
> deployment or registry promotion. A trusted external evidence and identity
> verifier is not implemented.

SkillsOps maps locally available governance records (security scan, RBAC,
policy events, HMAC audit, and modeled deployment records) to framework
controls. Collection uses no LLM or network, which makes it reproducible but
also limits it to evidence visible on the local machine.

## Frameworks

Declared as YAML in `skillctl/compliance/frameworks_data/`:

| ID | Name | Notes |
|----|------|-------|
| `eu-ai-act` | EU Artificial Intelligence Act (2024/1689) | High-risk obligations, effective 2026-08-02 |
| `iso-42001` | ISO/IEC 42001 | Control mapping only; not certification |
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

Every `EvidenceRecord` carries a SHA-256 `integrity_hash` of its content. This
detects accidental changes within the report pipeline; it does not prove source
authenticity. HMAC audit evidence is accepted only when its chain can be
verified with the configured key.

## Status & scoring

Per control: `COMPLIANT` (all required evidence is present and passes its
semantic checks), `PARTIALLY_COMPLIANT` (some valid evidence), `NON_COMPLIANT`
(insufficient or failed evidence), `PENDING_REVIEW` (human review required but
no verified attestation), and `NOT_APPLICABLE` (excluded by risk level). The
preview score weights compliant=1.0, partial=0.5, and pending/non-compliant=0.0
over applicable controls. An unacceptable-risk classification always scores
zero.

## Risk classification

`RiskClassifier` heuristically maps a skill to an EU AI Act risk level
(`UNACCEPTABLE`/`HIGH`/`LIMITED`/`MINIMAL`) using keyword analysis of the
metadata and deployment context. This keyword-based result requires qualified
human review before operational or legal use.

## Attestations

Controls needing human sign-off can be recorded in `AttestationStore` (SQLite):
time-bounded (default 90 days), superseded on re-attestation, and invalidated
when the skill version changes. CLI attestations have no authenticated signer
or signature and therefore remain pending; they cannot make a control compliant
or satisfy an enforcement gate.

## CLI

```bash
skillctl compliance frameworks                              # list frameworks
skillctl compliance classify ./my-skill                     # risk level
skillctl compliance report ./my-skill --framework eu-ai-act # full report (md|json)
skillctl compliance gaps ./my-skill --framework eu-ai-act   # non/partial controls
skillctl compliance attest --control art-9-2-b --skill ./my-skill \
    --statement "Residual risks reviewed and accepted per assessment v3"
```

A report exits non-zero unless every applicable control is fully satisfied.
Teams may use that as a conservative local quality check, but it is not a
regulatory compliance gate.
