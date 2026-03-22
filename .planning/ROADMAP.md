# Roadmap: skillctl

## Overview

skillctl delivers a governance-first CLI, registry, and evaluation platform for agent skills in three milestone releases. v0.1.0 covers the full "validate, evaluate, distribute" story: a CLI for local governance, a self-hosted registry for teams, and an eval suite with certification grading. v0.2.0 adds runtime enforcement via a skills gateway, pub/sub channels, and governance workflows. v0.3.0 closes the loop with automated skill optimization — using eval as a reward signal to iteratively improve skills without human supervision.

## Phases

**Phase Numbering:**
- Phases 1-3: v0.1.0 milestone
- Phases 4-5: v0.2.0 milestone
- Phases 6-7: v0.3.0 milestone
- Decimal phases (e.g. 2.1): Urgent insertions (marked with INSERTED)

- [ ] **Phase 1: CLI and Local Governance** - Single-developer tool: init, validate, scan, push/pull, diff, dependencies, local registry
- [ ] **Phase 2: Registry Server** - Self-hostable team registry with auth, publish/search, and audit logging
- [ ] **Phase 3: Eval Suite** - Eval engine with LLM-as-judge, certification grades, registry integration
- [ ] **Phase 4: Skills Gateway** - Policy enforcement proxy between agents and MCP servers
- [ ] **Phase 5: Pub/Sub, SDK, and Governance** - Channel distribution, TypeScript SDK, approval workflows, policy engine
- [ ] **Phase 6: Skill Optimizer** - Automated improvement loop: eval → failure analysis → variant generation → promotion
- [ ] **Phase 7: Optimization Governance** - Audit, provenance, cost controls, and registry integration for optimization runs

## Phase Details

### Phase 1: CLI and Local Governance
**Goal**: A single developer can create, validate, security-scan, version-diff, and locally store skills with full governance enforcement -- no network required
**Depends on**: Nothing (first phase)
**Requirements**: CLI-01 to CLI-07, FMT-01 to FMT-06, VAL-01 to VAL-06, SEC-01 to SEC-10, REG-01 to REG-05, DIF-01 to DIF-03, DEP-01 to DEP-03, QAL-01, QAL-04, QAL-05
**Success Criteria** (what must be TRUE):
  1. Developer can run `skillctl init && skillctl validate && skillctl push` on a new skill in under 2 minutes
  2. `skillctl scan` catches all 8 security detection patterns (SKL-S001 through SKL-S008) against the 50+ case test corpus
  3. `skillctl diff` between two skill versions correctly flags breaking changes (removed parameters, narrowed types) with visible red highlighting
  4. `skillctl doctor` diagnoses and reports all known environment failure modes
  5. Three or more example skills in /examples pass both `skillctl validate` and `skillctl scan`
**Plans**: TBD

Plans:
- [ ] 01-01: TBD
- [ ] 01-02: TBD
- [ ] 01-03: TBD

### Phase 2: Registry Server
**Goal**: Teams can deploy a self-hosted registry in under 10 minutes and use it to publish, search, and audit skills remotely
**Depends on**: Phase 1
**Requirements**: SRV-01 to SRV-11, QAL-02
**Success Criteria** (what must be TRUE):
  1. `docker compose up` starts a working registry server with zero external dependencies
  2. `skillctl publish` uploads a validated skill to the remote registry and `skillctl pull` retrieves it over HTTP
  3. `skillctl search` returns matching skills from the remote registry filtered by namespace, tag, or query string
  4. Every mutating operation (publish, delete) is recorded in the append-only audit log with actor, timestamp, and HMAC signature
  5. Token-based auth correctly scopes access: read-only tokens cannot publish, namespace-scoped tokens cannot write to other namespaces
**Plans**: TBD

Plans:
- [ ] 02-01: TBD
- [ ] 02-02: TBD

### Phase 3: Eval Suite
**Goal**: Developers can evaluate skill quality across safety, functional correctness, and trigger reliability — with A-F certification grades stored in the registry
**Depends on**: Phase 2 (registry integration for storing eval reports)
**Requirements**: EVL-01 to EVL-12, QAL-03, QAL-06, QAL-07, QAL-08
**Success Criteria** (what must be TRUE):
  1. `skillctl eval audit` scans a skill and produces an A-F grade using the 100-point scoring system
  2. `skillctl eval functional` compares agent performance with vs without a skill and reports quality delta
  3. `skillctl eval report` produces a unified score (40% audit, 40% functional, 20% trigger) with certification tier
  4. `skillctl eval regression` detects score drops between two skill versions and exits non-zero
  5. Eval reports are stored in the registry alongside skill metadata and retrievable via `skillctl show --eval`
**Plans**: TBD

Plans:
- [ ] 03-01: TBD
- [ ] 03-02: TBD

### Phase 4: Skills Gateway (v0.2.0)
**Goal**: Platform teams can deploy a policy enforcement proxy between agents and MCP servers that controls which agents can invoke which skills at runtime
**Depends on**: Phase 2 (registry as policy source)
**Requirements**: GW-01 to GW-07
**Success Criteria** (what must be TRUE):
  1. Gateway proxies agent-to-MCP-server traffic and enforces per-agent skill permissions
  2. Every skill invocation is logged with agent identity, skill name, input/output, and timestamp
  3. Unauthorized skill invocations are blocked and logged (not silently dropped)
  4. Gateway fetches allowed skills configuration from registry server
  5. OpenTelemetry spans emitted for all proxied requests
**Plans**: TBD

Plans:
- [ ] 04-01: TBD
- [ ] 04-02: TBD

### Phase 5: Pub/Sub, SDK, and Governance (v0.2.0)
**Goal**: Organizations can distribute skills via channels, integrate from TypeScript, and enforce approval workflows
**Depends on**: Phase 2
**Requirements**: PUB-01 to PUB-07, SDK-01 to SDK-03, GOV-01 to GOV-05
**Success Criteria** (what must be TRUE):
  1. Subscriber webhook receives `skill.published` event within 5 seconds of publish
  2. `@skillctl/sdk` npm package connects to registry and subscribes to channels
  3. Skills published without required approvals are rejected by registry
  4. `skillctl policy check` evaluates custom Rego rules against skill content
  5. `skillctl audit` shows full event history with time-range filtering
**Plans**: TBD

Plans:
- [ ] 05-01: TBD
- [ ] 05-02: TBD

### Phase 6: Skill Optimizer (v0.3.0)
**Goal**: Developers can run `skillctl optimize` to automatically improve a skill's eval score through iterative failure analysis and LLM-generated variants — overnight, unattended
**Depends on**: Phase 3 (eval suite provides scoring infrastructure)
**Requirements**: OPT-01 to OPT-12
**Success Criteria** (what must be TRUE):
  1. `skillctl optimize` reads eval failures, generates 3 variants, evaluates them, and promotes the best — in a single automated loop
  2. An optimization run of 10 iterations on a skill with 20 test scenarios completes without human intervention
  3. Plateau detection correctly halts optimization when 3 consecutive cycles produce no improvement
  4. Cost budget enforcement stops the run before exceeding the configured USD limit
  5. Every promoted variant has a full provenance chain: parent version → failure analysis → hypothesis → eval report → promotion decision
**Plans**: TBD

Plans:
- [ ] 06-01: TBD
- [ ] 06-02: TBD

### Phase 7: Optimization Governance (v0.3.0)
**Goal**: Platform teams have full visibility and control over automated skill optimization — audit trails, cost tracking, and publish gates
**Depends on**: Phase 6 (optimizer engine), Phase 2 (registry for audit/publish)
**Requirements**: OPG-01 to OPG-05
**Success Criteria** (what must be TRUE):
  1. Every optimization run is recorded in the audit log with trigger, iteration count, score delta, cost, and promoted version
  2. Optimized skills carry `metadata.optimized_from` linking to source version and run ID
  3. `--dry-run` executes the full loop without promoting or publishing any variant
  4. Registry rejects optimized skills that haven't passed a full eval after optimization (eval-gated publish)
**Plans**: TBD

Plans:
- [ ] 07-01: TBD

## Progress

**Execution Order:**
v0.1.0: 1 -> 2 -> 3
v0.2.0: 4, 5 (parallel, both depend on Phase 2)
v0.3.0: 6 (depends on Phase 3) -> 7 (depends on Phase 6 + Phase 2)

| Phase | Plans Complete | Status | Milestone | Completed |
|-------|----------------|--------|-----------|-----------|
| 1. CLI and Local Governance | 0/3 | Not started | v0.1.0 | - |
| 2. Registry Server | 0/2 | Not started | v0.1.0 | - |
| 3. Eval Suite | 0/2 | Not started | v0.1.0 | - |
| 4. Skills Gateway | 0/2 | Not started | v0.2.0 | - |
| 5. Pub/Sub, SDK, Governance | 0/2 | Not started | v0.2.0 | - |
| 6. Skill Optimizer | 0/2 | Not started | v0.3.0 | - |
| 7. Optimization Governance | 0/1 | Not started | v0.3.0 | - |
