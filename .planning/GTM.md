# skillctl — Go-to-Market & Business Strategy

## Positioning

**Tagline:** "What Terraform did for infrastructure, skillctl does for agent skills."

**Elevator pitch:** Agent skills grew to npm-scale in two months with zero governance. skillctl is the open-source CLI that gives platform teams validation, evaluation, security scanning, and audit trails for every skill touching production — across any agent runtime. Skills get an A-F certification grade before they reach consumers.

**Category:** Developer tools / Infrastructure governance

**Three pillars of v0.1.0:**
1. **Validate** — schema, security, and capability checks (`skillctl validate` + `skillctl scan`)
2. **Evaluate** — A-F certification grading with deterministic + LLM-as-judge scoring (`skillctl eval`)
3. **Distribute** — self-hosted registry with auth and audit (`skillctl publish`)

## Strategic Moat

**The format wins, skillctl wins.** The SKILL.md format and pub/sub protocol spec are published as open RFCs. If third parties implement compatible registries using the skillctl format, that's a win — the format becomes the standard regardless of competition.

This mirrors Terraform's HCL / HashiCorp's playbook: own the format, own the ecosystem.

## Target Users

| Segment | Priority | Pain | Entry Point |
|---------|----------|------|-------------|
| Platform teams | Primary | No way to review/approve/audit skills agents use in production | `skillctl validate` + `skillctl scan` in CI |
| AI engineers | Secondary | Skills scattered across docs, repos, Slack — no single source of truth | `skillctl init` + `skillctl push` for personal registry |

## Competitive Positioning

| Player | What They Do | What They Don't | skillctl's Angle |
|--------|-------------|-----------------|------------------|
| skills.sh (Vercel) | Discovery + marketplace | Enterprise governance, RBAC, private tenants | Drop-in governance layer — skills.sh skills work with skillctl |
| SkillsMP66 | Community aggregation | Centralized verification, security | Security scanning + validation that SkillsMP66 lacks |
| Anthropic/OpenAI native | Vendor org management | Cross-platform, independent marketplace | Vendor-neutral — works across all agent runtimes |
| Chainguard | Security hardening + audit | Governance, RBAC, versioning, marketplace | Full lifecycle governance, not just security |
| AWS Agent Skill Eval | Open-source eval framework (621 tests) | No governance, no registry, no CLI | Fork + extend — eval is a component of skillctl, not a competitor |

**Key threat:** Chainguard (entered March 17, 2026). They have security credibility. If they expand into governance/RBAC/versioning, they're the most dangerous competitor. **Speed matters.**

**Eval as differentiator:** Nobody else grades skills. skills.sh has a leaderboard but no quality assessment. Chainguard audits security but doesn't measure whether a skill actually improves agent performance. skillctl's A-F certification (40% audit + 40% functional + 20% trigger reliability) is the first standardized quality signal for agent skills. Research backs this: lightweight skills improve F1 by 34%, but heavyweight skills degrade performance (Melanie Li). Eval makes this measurable.

## Business Model (Future)

**Phase 1 — OSS adoption (current focus):**
- Free CLI + self-hosted registry
- Build format dominance and community
- No monetization — pure adoption play

**Phase 2 — SkillOS Cloud (future, not in current scope):**
Open-core model. Everything below is reserved for the paid managed platform:
- Multi-tenant isolation
- SSO/SAML/OIDC
- Skill certification badges + trust scores
- Marketplace with payment/revenue-share
- RL feedback loop + execution analytics
- Cross-tenant skill sharing
- SLA guarantees on pub/sub delivery
- Compliance packs (MiFID, HIPAA, Legal)

**Architectural constraint:** OSS codebase uses interfaces and extension points so cloud features are additive, not forks.

## Market Evidence

The pain is real and quantified:
- **Feb 2026:** RCE on Claude Code via repository config files
- **Feb 2026:** 1,184 malicious skills poisoned an agent marketplace
- **Feb 2026:** Thousands of MCP servers exposed without authentication
- **"What Would Elon Do?"** (most popular skill on ClawHub) was functional malware — exfiltrated data, bypassed safety via prompt injection, downloaded thousands of times
- npm took a decade to reach 350K packages. Agent skills did it in ~2 months. **Speed without governance = catastrophe.**

## Launch Criteria (v0.1.0)

Before public announcement:
- All Phases 1-3 complete (CLI + Registry + Eval)
- Zero known vulnerabilities in Go deps (govulncheck clean)
- Copy-paste quickstart for macOS + Linux
- Docker image at ghcr.io/skillctl/registry:0.1.0
- 3+ example skills passing validate + scan + eval (with A-F grades)
- GitHub Actions CI green on main
- At least one example eval suite demonstrating skill certification flow

---
*Extracted from CLAUDE.md on 2026-03-21. Updated 2026-03-22: eval positioning, AWS Agent Skill Eval fork, certification grading, revised launch criteria.*
