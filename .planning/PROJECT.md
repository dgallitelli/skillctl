# skillctl

## What This Is

An open-source governance platform for agent skills — "what Terraform did for infrastructure, skillctl does for agent skills." It gives platform teams a single tool to validate, evaluate, publish, audit, and enforce policy on skills across any agent runtime (Anthropic, OpenAI, Gemini). The platform has three layers: a CLI for local governance, a self-hostable registry server for team distribution, and an eval suite (forked from AWS Agent Skill Eval) that grades skills A-F across safety, quality, and reliability. A runtime gateway for policy enforcement is planned for v0.2.0.

## Core Value

**No skill reaches production without passing through a governance gate.** Every mutation is attributable, reversible, and auditable. This is the one thing that must work — if governance fails, the tool has no reason to exist.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Go CLI (`skillctl`) with cobra-based command structure
- [ ] `skill.yaml` manifest format (apiVersion, metadata, spec, governance sections)
- [ ] `skillctl init` — scaffold new skills with yaml + SKILL.md template
- [ ] `skillctl validate` — schema validation, semver check, capability declarations
- [ ] `skillctl scan` — 8 security detection rules (prompt injection, exfiltration, secrets, obfuscation)
- [ ] `skillctl push/pull` — local content-addressed storage (~/.skillctl/store/)
- [ ] `skillctl list` — local registry contents
- [ ] `skillctl diff` — version comparison with breaking change detection
- [ ] `skillctl.lock` — dependency lock file with integrity hashes
- [ ] `skillctl doctor` — environment diagnostics
- [ ] Self-hostable registry server (Go, filesystem + S3 backends, SQLite index)
- [ ] Token-based auth with scoped permissions (read, write:<ns>, admin)
- [ ] `skillctl publish/search` — remote registry interaction
- [ ] Eval suite (forked from AWS Agent Skill Eval) with A-F grading, LLM-as-judge, certification tiers
- [ ] Eval registry integration — reports stored alongside skill metadata
- [ ] Backward compatibility with plain SKILL.md files (auto-wrap in manifest)
- [ ] Append-only audit log (JSONL, HMAC-SHA256 signed)
- [ ] Certification grades: A/B = verified, C = community, D/F = rejected
- [ ] v0.2.0: Skills gateway (policy proxy between agents and MCP servers)
- [ ] v0.2.0: Pub/sub channels + TypeScript SDK + approval workflows
- [ ] v0.2.0: Agent identity (service tokens, OAuth 2.1/PKCE)
- [ ] v0.2.0: Observability (invocation logging, anomaly detection)

### Out of Scope

- Multi-tenant isolation — reserved for SkillOS Cloud
- SSO/SAML/OIDC — beyond basic token auth for OSS
- Skill certification badges and trust scores — cloud feature
- Marketplace with payment/revenue-share — cloud feature
- RL feedback loop and execution analytics — cloud feature
- Cross-tenant skill sharing — cloud feature
- SLA guarantees on pub/sub delivery — cloud feature
- Compliance pack bundles (MiFID, HIPAA, Legal) — cloud feature
- Mobile or web UI for the registry — CLI-first for OSS

## Context

### Market Context

The agent skills ecosystem grew from 0 to npm-scale in ~2 months (2026). This explosive growth has no governance infrastructure:

- **February 2026:** RCE via Claude Code repository config files, 1,184 malicious skills poisoning an agent marketplace, thousands of MCP servers exposed without authentication
- **"What Would Elon Do?"** — the most popular skill on ClawHub — was functional malware: exfiltrated data to attacker-controlled servers using prompt injection to bypass safety guidelines. Downloaded thousands of times.

**Competitive landscape:**

| Player | Angle | Gap |
|--------|-------|-----|
| skills.sh (Vercel) | Public directory + leaderboard, CLI install cross-agent | Zero enterprise governance, no RBAC, no private tenants |
| SkillsMP66 | 500+ community skills for Claude Code/Codex | No centralized verification, significant security risk |
| Anthropic/OpenAI native | Org-wide management, pre-built vendor skills | Vendor-locked, no cross-platform, no independent marketplace |
| Chainguard Agent Skills | Hardened catalog, security review, audit trail | Security-only — no governance/RBAC/versioning/marketplace |
| Vellum/Kore.ai/Voiceflow | Agent management with RBAC and audit | Agent lifecycle focus, not skills as distributable artifacts |

**Gap:** Chainguard does security-by-default. Vercel does discovery. Nobody does end-to-end enterprise governance (private registry + pub/sub + RBAC + compliance).

**Threat:** Chainguard entered March 17, 2026. If they expand from security into governance, it's a race.

### Technical Context

- Primary language: Go (CLI + registry server) — single binary, no runtime deps
- Secondary: TypeScript (SDK/protocol types) — ecosystem lingua franca
- CLI framework: cobra + viper + bubbletea
- Registry: net/http + chi router + SQLite + pluggable storage (filesystem/S3)
- Validation: JSON Schema (Draft 2020-12) + RE2 regex (linear time, no ReDoS)
- Audit: append-only JSONL, HMAC-SHA256 signed
- Testing: testify, httptest, go test -fuzz
- Auth tokens: OS keychain via zalando/go-keyring, fallback to SKILLCTL_TOKEN env var

### Target Users

1. **Platform teams** (primary) — building internal AI platforms, need to govern skills across org
2. **AI engineers** (secondary) — building with Claude/GPT, want to version and share skills

## Constraints

- **License:** MPL-2.0 — copyleft on files, permissive on linking (allows proprietary cloud layer)
- **Compatibility:** Must work with existing SKILL.md files from skills.sh / Anthropic native format
- **Architecture:** Interfaces and extension points everywhere — cloud layer adds features without forking
- **Security:** Prompt injection detection built into validator, not optional. Content-addressed storage (SHA-256)
- **DX:** < 60 seconds from brew install to first skill push. Human-readable errors with fix hints

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Go for CLI + server | Single binary, shared types/validation between CLI and server | -- Pending |
| MPL-2.0 license | Copyleft on files (protects format), permissive linking (enables cloud layer) | -- Pending |
| SKILL.md backward compat | Don't force rewrites — adopt governance without changing existing skills | -- Pending |
| OSS-first, cloud later | Build adoption and format dominance before monetizing | -- Pending |
| Content-addressed storage | Git-like integrity — prevents tampering, enables verification | -- Pending |
| EventBus interface for pub/sub | Cloud layer swaps in Kafka/SQS without touching business logic | -- Pending |
| RE2 regex engine | Guaranteed linear time — no ReDoS in security scanner | -- Pending |
| Fork AWS Agent Skill Eval (MIT-0) | Battle-tested eval with 621 tests; extend don't rebuild | -- Pending |
| Eval before pub/sub in v0.1.0 | Proving skills work > distributing them. Eval makes governance credible | -- Pending |
| Gateway in v0.2.0 (not v0.1.0) | Runtime enforcement is a separate concern from build-time governance | -- Pending |
| A-F grades = certification tiers | Reuse eval scoring as certification mechanism (verified/community/rejected) | -- Pending |

---
*Last updated: 2026-03-22 after scope revision (eval in v0.1.0, gateway in v0.2.0)*
