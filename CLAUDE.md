# CLAUDE.md — skillctl OSS Implementation Plan

> **Project:** `skillctl` — The open-source CLI and governance layer for Agent Skills  
> **Tagline:** "What Terraform did for infrastructure, skillctl does for agent skills."  
> **License:** Mozilla Public License 2.0 (MPL-2.0)  
> **Repo structure:** Monorepo (`skillctl/`)  
> **Primary language:** Go (CLI) + TypeScript (SDK/protocol types)  
> **Target runtimes:** Agent skills compatible with Anthropic, OpenAI, and any SKILL.md-based agent

---

## 0. Guiding Principles (Read First)

These principles MUST inform every architectural and UX decision throughout the project.

### 0.1 Governance-First Design
Governance is not a feature added on top — it is the architecture. Every component must be designed with the assumption that:
- Multiple humans with different roles (author, reviewer, approver, consumer) interact with skills
- Every mutation (publish, deprecate, update, delete) must be attributable, reversible, and auditable
- Trust must be explicitly granted, never implicitly assumed

Practically, this means:
- **No skill reaches a consumer without passing through a validation gate** (even in dev mode, validation is run and warnings shown)
- **Every operation that mutates state generates a structured log entry** (who, what, when, before, after)
- **Semver is mandatory** — skills without a valid semver tag cannot be pushed to any registry
- **Schemas are contracts** — breaking changes (removal of fields, parameter renames) must be flagged automatically

### 0.2 Developer Experience is Non-Negotiable
Following the HashiCorp "paved roads" philosophy: governance should feel like a time-saver, not a tax. The CLI must:
- Work in under 60 seconds from `brew install` to first `skill push`
- Provide human-readable errors with actionable fix hints (never raw stack traces)
- Have a `--dry-run` flag on every destructive or publishing command
- Include a `skillctl doctor` command that diagnoses environment issues
- Tab-complete all commands and subcommands (cobra + completion scripts)

### 0.3 Open Format, Open Protocol
The SKILL.md format and the pub/sub protocol spec MUST be:
- Published as open RFCs in `/docs/spec/`
- Vendor-neutral (compatible with Anthropic, OpenAI, Gemini agent formats)
- Designed so that a third party can implement a compatible registry without using `skillctl` code

This is the strategic moat: if the format wins, skillctl wins regardless of competition.

### 0.4 Security is Shift-Left
Following OWASP and DevSecOps principles:
- Static security analysis of skill content runs on every `skill validate` call
- Prompt injection pattern detection is built into the validator, not an optional plugin
- Skills that reference external URLs are flagged and require explicit `--allow-external` acknowledgment
- The local registry uses content-addressed storage (SHA-256 hashes) to prevent tampering

### 0.5 Compatibility Over Purity
Like OpenTofu with Terraform: be a drop-in complement, not a replacement. Skills written for `skills.sh` or Anthropic's native format must work with `skillctl` with zero or minimal changes. Never require users to rewrite working skills to adopt governance.

---

## 1. Repository Structure

```
skillctl/
├── CLAUDE.md                    # This file — implementation guide for Claude Code
├── README.md
├── LICENSE                      # MPL-2.0
├── CHANGELOG.md
├── go.work                      # Go workspace (multi-module)
│
├── cmd/
│   └── skillctl/
│       └── main.go              # CLI entrypoint (cobra)
│
├── internal/
│   ├── cli/                     # Command implementations
│   │   ├── init.go
│   │   ├── push.go
│   │   ├── pull.go
│   │   ├── publish.go
│   │   ├── validate.go
│   │   ├── diff.go
│   │   ├── list.go
│   │   ├── deprecate.go
│   │   ├── doctor.go
│   │   └── version.go
│   ├── registry/                # Local registry engine
│   │   ├── store.go             # Content-addressed storage
│   │   ├── index.go             # Skill index and search
│   │   ├── lock.go              # skillctl.lock file management
│   │   └── remote.go            # Remote registry client interface
│   ├── skill/                   # Skill domain model
│   │   ├── schema.go            # SkillManifest struct + parsing
│   │   ├── validator.go         # Validation engine
│   │   ├── security.go          # Prompt injection + exfiltration detection
│   │   ├── semver.go            # Version management
│   │   └── diff.go              # Diff computation between versions
│   ├── pubsub/                  # Pub/Sub protocol implementation
│   │   ├── channel.go
│   │   ├── subscriber.go
│   │   └── event.go
│   ├── audit/                   # Immutable audit log
│   │   ├── logger.go
│   │   └── entry.go
│   └── config/                  # Configuration management
│       ├── config.go
│       └── auth.go
│
├── pkg/                         # Public SDK (importable by third parties)
│   ├── skillsdk/
│   │   ├── client.go            # Registry client SDK (Go)
│   │   └── types.go
│   └── skillspec/               # Skill format types (shared)
│       ├── manifest.go
│       └── channel.go
│
├── sdk/
│   └── typescript/              # TypeScript SDK
│       ├── src/
│       │   ├── client.ts
│       │   ├── types.ts
│       │   └── validator.ts
│       ├── package.json
│       └── tsconfig.json
│
├── registry-server/             # Self-hostable registry HTTP server
│   ├── main.go
│   ├── api/
│   │   ├── routes.go
│   │   ├── skills.go
│   │   ├── channels.go
│   │   └── audit.go
│   ├── storage/
│   │   ├── filesystem.go        # Default: local filesystem
│   │   └── s3.go                # Optional: S3-compatible backend
│   └── auth/
│       ├── token.go
│       └── middleware.go
│
├── docs/
│   ├── spec/
│   │   ├── SKILL_FORMAT.md      # Open RFC: SKILL.md format specification
│   │   ├── PUBSUB_PROTOCOL.md   # Open RFC: pub/sub protocol specification
│   │   └── REGISTRY_API.md      # Open RFC: registry HTTP API specification
│   ├── getting-started.md
│   ├── governance-guide.md
│   └── self-hosting.md
│
├── examples/
│   ├── basic-skill/
│   ├── versioned-skill/
│   └── channel-subscription/
│
├── action/                      # GitHub Action
│   ├── action.yml
│   └── entrypoint.sh
│
└── test/
    ├── integration/
    ├── fixtures/
    └── security/
        └── prompt_injection_corpus.txt   # Test corpus for security scanner
```

---

## 2. Core Data Formats

### 2.1 skill.yaml — The Skill Manifest

Every skill MUST have a `skill.yaml` at its root. This is the canonical manifest.

```yaml
# skill.yaml — required fields
apiVersion: skillctl.io/v1
kind: Skill

metadata:
  name: my-org/code-reviewer          # <namespace>/<skill-name>
  version: 1.2.0                       # semver, mandatory
  description: "Reviews PRs for security issues and code quality"
  authors:
    - name: "Davide G."
      email: "davide@example.com"
  license: MIT
  tags: ["security", "code-review", "engineering"]
  created: 2026-03-01T00:00:00Z
  updated: 2026-03-21T00:00:00Z

spec:
  # The actual skill content (inline or path reference)
  content:
    type: markdown               # markdown | json | yaml
    path: ./SKILL.md             # relative path OR inline below
    # inline: |
    #   ## Code Reviewer
    #   When asked to review code...

  # Input parameters the skill accepts
  parameters:
    - name: language
      type: string
      required: false
      default: "auto-detect"
      description: "Programming language to focus on"
    - name: strictness
      type: enum
      values: ["low", "medium", "high"]
      default: "medium"

  # Declared capabilities — what this skill can do
  capabilities:
    - read_file
    - read_code
    # NOTE: write_file, network_access, exec must be explicitly declared
    # Any undeclared capability used triggers a validation warning

  # External dependencies on other skills
  dependencies:
    - name: my-org/base-engineering
      version: ">=1.0.0 <2.0.0"

  # Compatibility declarations
  compatibility:
    agents:
      - anthropic/claude
      - openai/gpt-4
      - any                       # 'any' = agent-agnostic
    skillctl: ">=0.1.0"

governance:
  # Approval requirements (enforced by registry-server in managed mode)
  approvals:
    required: 1                   # minimum approvals before publish
    from: ["owner", "admin"]      # roles that can approve
  
  # Channel subscriptions this skill publishes to
  channels:
    - my-org/engineering
    - my-org/security

  # Deprecation policy
  deprecation:
    policy: "min-support-months: 6"   # at least 6 months of support after deprecation notice
```

### 2.2 skillctl.lock — Dependency Lock File

Generated automatically by `skillctl install`. MUST be committed to version control.

```yaml
# skillctl.lock — DO NOT EDIT MANUALLY
# Generated by skillctl v0.1.0 on 2026-03-21T10:00:00Z
lockfileVersion: 1

skills:
  my-org/base-engineering:
    version: 1.4.2
    resolved: "https://registry.skillctl.io/my-org/base-engineering/1.4.2"
    integrity: sha256:a1b2c3d4e5f6...
    
  community/summarizer:
    version: 2.1.0
    resolved: "https://registry.skillctl.io/community/summarizer/2.1.0"
    integrity: sha256:f6e5d4c3b2a1...
```

### 2.3 .skillctlrc — Workspace Config

```yaml
# .skillctlrc — project-level configuration (commit this)
registry:
  default: https://registry.skillctl.io
  private:
    - name: my-org
      url: https://skills.mycompany.com
      namespace: my-org/*

auth:
  tokenEnvVar: SKILLCTL_TOKEN      # name of env var holding auth token

governance:
  requireApproval: true            # block push without approval in CI
  requireTests: true               # block push without passing tests
  auditLog: true                   # write local audit log on all ops
```

---

## 3. CLI Commands — Full Specification

All commands follow the pattern: `skillctl <verb> [target] [flags]`

### 3.1 Core Lifecycle Commands

```bash
# Initialize a new skill in current directory
skillctl init [skill-name] [--namespace my-org]
# Creates: skill.yaml, SKILL.md (template), .skillignore

# Validate a skill (runs all checks, no network required)
skillctl validate [path] [--strict] [--json]
# Exit 0 = valid, exit 1 = errors, exit 2 = warnings only

# Run security scan on skill content
skillctl scan [path] [--report json|text] [--fail-on warning|error]
# Detects: prompt injection patterns, data exfiltration patterns,
#          hardcoded secrets, undeclared external network calls

# Push skill to local registry (staging area)
skillctl push [path] [--dry-run] [--force]
# Validates, signs, stores in local content-addressed store
# Does NOT publish to remote — use 'publish' for that

# Publish skill to remote registry
skillctl publish [namespace/name@version] [--registry url] [--dry-run]
# Requires: passing validate + scan, auth token, approval if governance.requireApproval=true

# Pull skill from registry
skillctl pull [namespace/name@version] [--registry url] [--output path]

# Install all skills declared in skill.yaml dependencies
skillctl install [--frozen]   # --frozen = fail if lock would change
```

### 3.2 Discovery & Information Commands

```bash
# List skills in local registry
skillctl list [--namespace my-org] [--tag security] [--json]

# Search remote registry
skillctl search [query] [--registry url] [--tag t] [--json]

# Show full skill details
skillctl show [namespace/name@version] [--json]

# Show skill diff between two versions
skillctl diff [namespace/name@v1] [namespace/name@v2] [--format unified|side-by-side]
# Highlights: parameter changes, capability changes, content changes
# Flags: breaking changes (removed params, narrowed types) in RED
```

### 3.3 Governance Commands

```bash
# Deprecate a skill version or entire skill
skillctl deprecate [namespace/name@version] \
  --message "Use my-org/new-reviewer@2.0.0 instead" \
  --sunset 2026-09-21   # date after which skill is removed from registry

# Approve a pending skill (requires 'approver' role)
skillctl approve [namespace/name@version] [--message "LGTM"]

# View audit log
skillctl audit [namespace/name] [--since 7d] [--format json|table]
# Shows: push, publish, approve, deprecate, pull events with actor + timestamp

# Check a skill against a policy file (Sentinel-style)
skillctl policy check [skill-path] --policy [policy-file.rego]
```

### 3.4 Channel (Pub/Sub) Commands

```bash
# Subscribe to a channel (saves to .skillctlrc)
skillctl channel subscribe [org/channel-name]
# Effect: new publishes to this channel trigger skillctl install --update automatically

# List available channels
skillctl channel list [--namespace my-org]

# Publish a skill to a channel
skillctl channel publish [namespace/name@version] --channel [org/channel]

# Show channel subscribers and their pinned versions
skillctl channel status [org/channel-name]
```

### 3.5 Registry Server Commands

```bash
# Start self-hosted registry server
skillctl server start [--port 8080] [--storage filesystem|s3] [--auth-disabled]

# Check registry health and connectivity
skillctl doctor [--registry url]
# Checks: auth token valid, registry reachable, local store integrity,
#         dependencies resolvable, no expired skills in use
```

---

## 4. Registry Server API — HTTP Spec

The registry server exposes a REST API. The full OpenAPI spec is in `/docs/spec/REGISTRY_API.md`.

### Core Endpoints

```
# Skills
GET    /v1/skills                          # list skills (paginated, filterable)
GET    /v1/skills/{namespace}/{name}        # get skill metadata
GET    /v1/skills/{namespace}/{name}/versions  # list all versions
GET    /v1/skills/{namespace}/{name}/{ver} # get specific version
POST   /v1/skills/{namespace}/{name}       # publish new version (auth required)
DELETE /v1/skills/{namespace}/{name}/{ver} # deprecate version (auth required)

# Channels
GET    /v1/channels                        # list channels
GET    /v1/channels/{namespace}/{name}     # get channel details + subscribers
POST   /v1/channels/{namespace}/{name}/subscribe   # subscribe to channel
POST   /v1/channels/{namespace}/{name}/publish     # publish skill to channel

# Audit
GET    /v1/audit                           # query audit log (paginated)
GET    /v1/audit/{namespace}/{name}        # audit log for specific skill

# Auth
POST   /v1/auth/token                      # issue token
DELETE /v1/auth/token                      # revoke token

# Health
GET    /healthz                            # liveness probe
GET    /readyz                             # readiness probe
```

### Auth Model
- Token-based: `Authorization: Bearer <token>` header
- Tokens are scoped: `read`, `write:<namespace>`, `admin`
- `--auth-disabled` flag available for local dev only (emits warning on startup)

---

## 5. Pub/Sub Protocol Specification

The pub/sub protocol is transport-agnostic. The canonical implementation uses HTTP long-poll + webhooks. A WebSocket transport is defined in the spec but optional for implementors.

### Event Schema

```json
{
  "eventId": "evt_01HXYZ...",
  "type": "skill.published",
  "timestamp": "2026-03-21T10:00:00Z",
  "channel": "my-org/engineering",
  "skill": {
    "namespace": "my-org",
    "name": "code-reviewer",
    "version": "1.3.0",
    "previousVersion": "1.2.0"
  },
  "actor": {
    "id": "user_abc",
    "type": "human"
  },
  "meta": {
    "breaking": false,
    "deprecates": null
  }
}
```

### Event Types
- `skill.published` — new version published to channel
- `skill.deprecated` — version marked deprecated
- `skill.removed` — version removed after sunset date
- `skill.approved` — pending version received required approvals
- `channel.updated` — channel metadata changed

### Subscriber Behavior
When a subscriber receives a `skill.published` event:
1. Compare new version against current pinned version in `skillctl.lock`
2. If version satisfies declared constraint → auto-update lock file
3. If breaking change detected → block auto-update, emit alert, require human review
4. Dispatch `skillctl install --update <skill>` in CI pipeline via webhook

---

## 6. Security Scanner — Detection Rules

The `skillctl scan` command implements the following detection rules. Each rule has an ID, severity (WARNING/ERROR), and remediation hint.

```
SKL-S001  ERROR    Prompt injection pattern: instruction override attempt
          Pattern: /ignore (all )?(previous|prior|above) instructions/i
          Remediation: Remove or rewrite this section — it may be used to hijack agent behavior

SKL-S002  ERROR    Prompt injection pattern: role escalation
          Pattern: /you are now|act as|pretend (to be|you are)/i
          Remediation: Skills should not redefine the agent's identity

SKL-S003  ERROR    Data exfiltration: hardcoded URL with data parameters
          Pattern: External URL with query params containing {{...}} template expressions
          Remediation: Remove external URLs from skill content

SKL-S004  WARNING  Hardcoded secret pattern
          Pattern: /api[_-]?key|secret|password|token/i in value context
          Remediation: Use parameter references instead of hardcoded secrets

SKL-S005  WARNING  Undeclared capability: file system write
          Pattern: Skill content references file write operations but capability not declared
          Remediation: Add 'write_file' to spec.capabilities in skill.yaml

SKL-S006  WARNING  Undeclared external network access
          Pattern: URL patterns in skill content, not declared in capabilities
          Remediation: Add 'network_access' to spec.capabilities and document URLs

SKL-S007  ERROR    Encoding obfuscation detected
          Pattern: Base64 or hex-encoded blocks within skill instructions
          Remediation: Skills must be human-readable; obfuscated content is rejected

SKL-S008  WARNING  Skill size exceeds recommended limit (50KB)
          Remediation: Consider splitting into multiple focused skills with dependencies
```

---

## 7. Milestone Plan

> **SCOPE REVISION (2026-03-22):** The original milestones below (0–5) were written before the scope revision. The active roadmap is in `.planning/ROADMAP.md` which reorganizes work into 6 phases across 2 milestone releases:
> - **v0.1.0** (Phases 1–3): CLI + Local Governance → Registry Server → Eval Suite
> - **v0.2.0** (Phases 4–6): Skills Gateway → Pub/Sub + SDK + Governance → Agent Identity + Observability
>
> Key change: Eval Suite (Section 12) moved into v0.1.0 (Phase 3). Pub/Sub + Channels (original Milestone 3) deferred to v0.2.0 (Phase 5). The milestone specs below remain as detailed reference for implementation requirements.

### Milestone 0 — Foundation (Week 1–2)
**Goal:** Compiling CLI, working local registry, single-developer usable

**Deliverables:**
- [ ] Repo initialized with monorepo structure above
- [ ] `skillctl init` — creates skill.yaml + SKILL.md template
- [ ] `skillctl validate` — schema validation + semver check
- [ ] `skillctl push` — stores skill in local content-addressed store (`~/.skillctl/store/`)
- [ ] `skillctl pull` — retrieves from local store
- [ ] `skillctl list` — lists local registry contents
- [ ] `skillctl version` — prints version + build info
- [ ] Basic test suite (>60% coverage on core packages)
- [ ] README with 5-minute quickstart

**Definition of Done:** A developer can run `skillctl init && skillctl validate && skillctl push` on a new skill in < 2 minutes.

---

### Milestone 1 — Security & Diff (Week 3–4)
**Goal:** Governance primitives that developers actually feel

**Deliverables:**
- [ ] `skillctl scan` — implement all 8 detection rules (SKL-S001 through SKL-S008)
- [ ] `skillctl diff` — unified diff between two skill versions, with breaking change detection
- [ ] `skillctl.lock` — lock file generation and `skillctl install --frozen`
- [ ] Dependency resolution engine (basic: resolve, validate semver constraints)
- [ ] `skillctl doctor` — environment diagnostics
- [ ] Security test corpus (`test/security/prompt_injection_corpus.txt`) with 50+ test cases
- [ ] `--json` output flag on all commands (for CI integration)

**Definition of Done:** `skillctl scan` catches all 8 injection/exfiltration patterns in test corpus. `skillctl diff` correctly flags breaking changes (removed parameters).

---

### Milestone 2 — Registry Server (Week 5–7)
**Goal:** Self-hostable registry that teams can deploy in 10 minutes

**Deliverables:**
- [ ] `registry-server/` — HTTP server implementing all `/v1/skills` endpoints
- [ ] Filesystem storage backend (default)
- [ ] Token-based auth (issue, validate, revoke)
- [ ] `skillctl publish` — pushes validated skill to remote registry
- [ ] `skillctl search` — queries remote registry
- [ ] Docker image: `ghcr.io/skillctl/registry:latest`
- [ ] `docker-compose.yml` for local dev with registry + UI stub
- [ ] Audit log: every mutating operation logged to append-only JSONL file
- [ ] Integration tests against live registry server

**Definition of Done:** `docker compose up` starts a working registry. `skillctl publish` successfully uploads a skill and `skillctl pull` retrieves it. Audit log records both operations.

---

### Milestone 3 — Pub/Sub + Channels (Week 8–10)
**Goal:** Teams can subscribe to skill channels and receive updates automatically

**Deliverables:**
- [ ] Channel management endpoints in registry server
- [ ] `skillctl channel subscribe/list/publish/status` commands
- [ ] Event schema + publisher (registry emits events on skill lifecycle changes)
- [ ] Webhook subscriber support (registry POSTs events to subscriber URL)
- [ ] Auto-update behavior: `skillctl install --update` triggered by channel event
- [ ] Breaking change protection: channel update blocked if breaking change detected without `--force-breaking`
- [ ] Event replay: subscribers can request missed events (up to 30 days)
- [ ] TypeScript SDK: `skillsdk` with channel subscription support

**Definition of Done:** Subscriber webhook receives `skill.published` event within 5 seconds of `skillctl publish`. Breaking change in new version is detected and blocks auto-update.

---

### Milestone 4 — Governance Layer (Week 11–13)
**Goal:** Enterprise-ready approval workflows and policy enforcement

**Deliverables:**
- [ ] Approval workflow in registry server (skill lands in `pending` state, requires N approvals)
- [ ] `skillctl approve` command + role-based authorization
- [ ] `skillctl deprecate` command with sunset date enforcement
- [ ] `skillctl audit` command with time-range filtering and JSON export
- [ ] OPA/Rego policy evaluation engine (`skillctl policy check`)
- [ ] 3 example policy files: `no-network-skills.rego`, `require-tests.rego`, `version-freeze.rego`
- [ ] SCIM-compatible user/role provisioning endpoint (stub, for enterprise integration)
- [ ] S3-compatible storage backend for registry server

**Definition of Done:** A skill published without required approvals is rejected by the registry. `skillctl audit` shows full history. Policy check correctly enforces custom Rego rules.

---

### Milestone 5 — GitHub Action + DX Polish (Week 14–15)
**Goal:** Zero-friction CI/CD integration, project ready for public launch

**Deliverables:**
- [ ] GitHub Action (`action/action.yml`): runs validate + scan + publish on push
- [ ] Example workflow: `.github/workflows/skillctl.yml`
- [ ] Tab completion for bash, zsh, fish (`skillctl completion <shell>`)
- [ ] `skillctl init --template <template>` with 5 built-in templates (basic, security, analyst, coder, researcher)
- [ ] Full documentation site structure (`docs/`)
- [ ] Open RFC documents: `SKILL_FORMAT.md`, `PUBSUB_PROTOCOL.md`, `REGISTRY_API.md`
- [ ] CONTRIBUTING.md with governance model for the OSS project itself
- [ ] `examples/` directory with 3 complete worked examples
- [ ] End-to-end test suite covering full lifecycle
- [ ] Performance: `skillctl validate` completes in < 500ms on a 100KB skill

**Definition of Done:** A new contributor can follow README, install skillctl, and publish their first skill to a self-hosted registry in under 15 minutes. GitHub Action passes on first run with a valid skill.

---

## 8. Technology Choices & Rationale

### CLI: Go
- Single binary distribution, no runtime dependencies
- Excellent CLI framework ecosystem (cobra, viper, bubbletea for TUI)
- Strong concurrency model for parallel dependency resolution
- Cross-platform: darwin/amd64, darwin/arm64, linux/amd64, linux/arm64, windows/amd64

### Registry Server: Go
- Same language as CLI = shared types and validation logic
- `net/http` + `chi` router: fast, minimal, easy to audit
- SQLite for metadata index (file-based, zero external dependencies for self-host)
- Pluggable storage interface for filesystem and S3

### TypeScript SDK
- Primary language of the agent/LLM tooling ecosystem
- Published to npm as `@skillctl/sdk`
- Shares type definitions with CLI via generated code from Go structs

### Validation
- JSON Schema (Draft 2020-12) for `skill.yaml` structure validation
- Custom Go rules for semantic validation (semver, capability declarations)
- RE2 regex engine for security pattern matching (guaranteed linear time, no ReDoS)

### Audit Log
- Append-only JSONL file (local) or database table (server)
- Each entry signed with HMAC-SHA256 using registry server key
- Export to SIEM-compatible formats (CEF, JSON) via `skillctl audit --export`

### Testing
- Go: `testify` for assertions, `httptest` for server integration tests
- Security corpus: 50+ prompt injection and exfiltration examples
- Fuzzing: `go test -fuzz` on skill parser and security scanner inputs

---

## 9. Non-Goals for OSS (Explicitly Out of Scope)

The following are intentionally NOT built in the OSS version. They are reserved for the managed SkillOS Cloud platform:

- Multi-tenant isolation (OSS registry serves a single organization)
- SSO/SAML/OIDC integration beyond basic token auth
- Skill certification badges and trust scores
- Marketplace with payment/revenue-share
- RL feedback loop and execution analytics
- Cross-tenant skill sharing with permission models
- SLA guarantees on pub/sub delivery
- Compliance pack bundles (MiFID, HIPAA, Legal)

The OSS codebase MUST be architected to make these features addable in the cloud layer without forking — use interfaces and extension points, not hardcoded behavior.

---

## 10. Definition of "Launch-Ready" (Public v0.1.0)

Before tagging v0.1.0 and announcing publicly, ALL of the following must be true:

- [ ] All v0.1.0 phases complete (Phase 1: CLI + Local Governance, Phase 2: Registry Server, Phase 3: Eval Suite)
- [ ] `skillctl validate` and `skillctl scan` pass on 100% of test fixture corpus
- [ ] `skillctl eval` produces A-F certification grades for example skills
- [ ] Zero known security vulnerabilities in Go dependencies (`govulncheck ./...` clean)
- [ ] README includes copy-paste quickstart that works on macOS + Linux
- [ ] License headers present in all source files
- [ ] CHANGELOG.md up to date
- [ ] Docker image pushes to `ghcr.io/skillctl/registry:0.1.0`
- [ ] GitHub Actions CI passes on PR merge to `main`
- [ ] `skillctl doctor` successfully detects and reports all known failure modes
- [ ] At least 3 example skills in `/examples` that pass `skillctl validate`, `skillctl scan`, and `skillctl eval`

---

## 11. Implementation Notes for Claude Code

When implementing this project, follow these specific directives:

1. **Follow the phase roadmap in `.planning/ROADMAP.md`** — do not implement features from later phases until the current one's Definition of Done is met. (Original milestone numbering in Section 7 is reference material; phases are the active execution plan.)

2. **Implement `skillctl validate` before `skillctl push`** — no skill should ever be stored without passing validation.

3. **Make the error messages exceptional.** Every `fmt.Errorf` that surfaces to the user should have: (a) what went wrong, (b) why it matters, (c) how to fix it. Use structured error types, not bare strings.

4. **The `skill.yaml` parser must be backward-compatible with plain `SKILL.md` files** — detect if the input is a raw markdown file (no `skill.yaml` present) and auto-wrap it in a minimal manifest with warnings.

5. **Content-addressed storage in local registry:** use `~/.skillctl/store/<sha256-prefix>/<sha256-full>` as the storage path. This mirrors how `git` objects work.

6. **Never store plaintext auth tokens in config files.** Use OS keychain (macOS Keychain, Linux Secret Service, Windows Credential Manager) via the `zalando/go-keyring` library. Fall back to env var `SKILLCTL_TOKEN`.

7. **All HTTP calls to remote registries must respect `HTTPS_PROXY` and `NO_PROXY` environment variables** — required for enterprise environments.

8. **The pub/sub event bus in the registry server must be replaceable** — implement behind an `EventBus` interface so the cloud version can swap in Kafka/SQS without touching business logic.

9. **The security scanner (SKL-S001 through SKL-S008) must be fuzz-tested** — add fuzz targets in `test/security/` before Milestone 1 is marked done.

10. **Go module path:** `github.com/skillctl/skillctl`. Assume the GitHub org `skillctl` exists. If not, replace with actual org name when known.

---

## 12. Skill Evaluation Suite (`skillctl eval`)

### 12.0 Why Eval Is a First-Class Feature

Governance without measurement is compliance theater. The eval suite answers the core question that no other tool in the skills ecosystem addresses:

> **"Does this skill actually make my agent better at its goal — and by how much?"**

This is not a testing framework for the skill's internal logic. It is a controlled experiment engine that runs an agent with and without a skill (or between two skill versions) against user-defined goals, and produces a side-by-side comparison with structured, auditable results.

The design is informed by three principles from current evaluation research:

- **Outcome over trajectory:** Agents often reach correct results via different paths. Evaluation must define success in terms of goal achievement, not specific action sequences. *(Source: multi-step agent eval best practices)*
- **Three evaluation layers:** Final output quality, component-level behavior (tool selection, reasoning), and LLM-level performance are distinct and must be measured independently.
- **Traceability is mandatory:** Every score must be linkable to the exact skill version, agent configuration, model, and dataset that produced it. An eval result without full provenance is worthless for governance.

---

### 12.1 Concepts & Terminology

| Term | Definition |
|---|---|
| **EvalSuite** | A named, versioned collection of scenarios and a goal definition |
| **Scenario** | A single test case: an input prompt + optional context + optional ground truth |
| **Goal** | User-defined success criteria for the suite (what "good" looks like) |
| **Run** | One execution of an EvalSuite against a specific agent+skill configuration |
| **Variant** | A configuration under test: `baseline` (no skill), `with_skill@version`, or `skill_a vs skill_b` |
| **Judge** | The grading mechanism: deterministic, LLM-as-judge, or human |
| **Report** | Structured output of a Run: per-scenario scores, delta, traces, and a pass/fail verdict |

---

### 12.2 EvalSuite File Format (`evalsuit.yaml`)

```yaml
# evalsuit.yaml — lives alongside skill.yaml in the skill repo
apiVersion: skillctl.io/v1
kind: EvalSuite

metadata:
  name: my-org/code-reviewer-eval
  version: 1.0.0
  skill: my-org/code-reviewer    # the skill under evaluation

# The user-defined goal: what does "good" look like for this skill?
goal:
  description: |
    The agent should identify security vulnerabilities in code snippets
    and suggest concrete remediation steps. Responses must be actionable,
    specific to the code shown, and not produce false positives on safe code.
  
  # How success is measured — multiple judges can be composed
  judges:
    - type: llm                   # LLM-as-judge
      model: claude-sonnet-4-20250514
      rubric: |
        Score the response 0-10 on:
        - Accuracy: Does it correctly identify real vulnerabilities? (0-4 pts)
        - Specificity: Are suggestions tied to the actual code? (0-3 pts)
        - False positive rate: Does it flag safe code as vulnerable? (0-3 pts, deduct 1 per false positive)
      output: score_0_10

    - type: deterministic         # rule-based check
      checks:
        - assert_contains_any: ["remediation", "fix", "replace", "use instead"]
        - assert_not_contains: ["I cannot", "I'm unable to"]
        - max_tokens: 2000        # response should be concise

    - type: human                 # optional: gate on human review
      required: false             # set true to block report until reviewed
      prompt: "Does this response feel useful to a real developer?"

# Evaluation scenarios (the test cases)
scenarios:
  - id: sqli_basic
    description: "SQL injection in raw query string"
    input: |
      Review this Python code:
      ```python
      def get_user(username):
          query = f"SELECT * FROM users WHERE name = '{username}'"
          return db.execute(query)
      ```
    ground_truth: "SQL injection vulnerability via f-string interpolation"
    expected_score_min: 7        # fail if LLM judge scores below 7

  - id: safe_code_no_fp
    description: "Safe code — should produce NO vulnerability findings"
    input: |
      Review this Python code:
      ```python
      def get_user(user_id: int):
          return db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
      ```
    ground_truth: "No vulnerability — parameterized query is safe"
    expected_verdict: clean      # fail if any vulnerability is flagged

  - id: xss_react
    description: "XSS via dangerouslySetInnerHTML"
    input: |
      Review this React component:
      ```jsx
      function Comment({ content }) {
        return <div dangerouslySetInnerHTML={{ __html: content }} />;
      }
      ```
    ground_truth: "XSS via unsanitized dangerouslySetInnerHTML"
    expected_score_min: 6

# Run configuration
run:
  variants:
    - id: baseline
      skill: null                  # agent with NO skill attached
    - id: with_skill_v1
      skill: my-org/code-reviewer@1.2.0
    - id: with_skill_v2
      skill: my-org/code-reviewer@1.3.0   # candidate version under review

  agent:
    model: claude-sonnet-4-20250514
    system_prompt: "You are a helpful engineering assistant."
    # skill is injected automatically per variant by skillctl eval runner

  concurrency: 3                   # run up to 3 scenarios in parallel
  timeout_per_scenario: 60s
  retries: 1                       # retry on transient API errors
```

---

### 12.3 CLI Commands

```bash
# Run the full eval suite against all variants
skillctl eval run [evalsuit.yaml] [--registry url] [--output report.json]

# Run a specific variant only
skillctl eval run [evalsuit.yaml] --variant with_skill_v2

# Run eval in CI mode: exit non-zero if any scenario fails expected_score_min
skillctl eval run [evalsuit.yaml] --ci --fail-threshold 0.8

# Show the side-by-side comparison report from a previous run
skillctl eval report [report.json] [--format table|json|html]

# Diff two eval reports (e.g. v1 vs v2 across the same scenarios)
skillctl eval diff [report-v1.json] [report-v2.json]

# List all eval runs for a skill (requires remote registry)
skillctl eval history [namespace/skill-name] [--limit 10]

# Add a new scenario interactively (prompts for input, expected output, grade)
skillctl eval add-scenario [evalsuit.yaml]

# Validate evalsuit.yaml schema
skillctl eval validate [evalsuit.yaml]
```

---

### 12.4 Eval Runner Architecture

```
skillctl eval run
       │
       ├── Parse & validate evalsuit.yaml
       │
       ├── For each Variant (baseline, with_skill@v1, with_skill@v2):
       │     │
       │     ├── Build agent config (model + system prompt + skill content if present)
       │     │
       │     └── For each Scenario (parallel, up to concurrency limit):
       │           │
       │           ├── Execute agent call → capture full response + trace
       │           │     (trace = all tool calls, reasoning steps, token counts)
       │           │
       │           └── Run all Judges against (response, scenario, ground_truth):
       │                 ├── Deterministic judge → pass/fail + reason
       │                 ├── LLM-as-judge → score 0-10 + rubric breakdown
       │                 └── Human judge → queued for async review (optional)
       │
       ├── Aggregate results into EvalReport
       │     ├── Per-scenario: score per variant + delta + pass/fail
       │     ├── Per-variant: mean score, pass rate, p50/p95 latency, cost estimate
       │     └── Suite-level: winner variant + improvement delta + recommendation
       │
       └── Write report.json (structured) + print summary table to stdout
```

**Key implementation note:** The agent execution in the eval runner MUST use the same agent API client as production (not a mock). Eval results are only meaningful if they reflect real agent behavior with real skill content injected into the system prompt or tool configuration.

---

### 12.5 Report Format (`report.json`)

```json
{
  "meta": {
    "evalSuite": "my-org/code-reviewer-eval@1.0.0",
    "runId": "run_01HXZ...",
    "timestamp": "2026-03-21T10:00:00Z",
    "skillctl": "0.1.0"
  },
  "variants": [
    {
      "id": "baseline",
      "skill": null,
      "scores": { "mean": 3.2, "p50": 3.0, "passRate": 0.33 },
      "cost_usd": 0.024,
      "latency_p50_ms": 1200
    },
    {
      "id": "with_skill_v2",
      "skill": "my-org/code-reviewer@1.3.0",
      "scores": { "mean": 8.1, "p50": 8.5, "passRate": 0.89 },
      "cost_usd": 0.041,
      "latency_p50_ms": 1850
    }
  ],
  "scenarios": [
    {
      "id": "sqli_basic",
      "results": {
        "baseline":      { "score": 2.0, "pass": false, "latency_ms": 1100, "trace_ref": "tr_001" },
        "with_skill_v2": { "score": 9.0, "pass": true,  "latency_ms": 1900, "trace_ref": "tr_002" }
      },
      "delta": +7.0,
      "winner": "with_skill_v2"
    },
    {
      "id": "safe_code_no_fp",
      "results": {
        "baseline":      { "score": 8.0, "pass": true,  "verdict": "clean", "latency_ms": 900 },
        "with_skill_v2": { "score": 9.5, "pass": true,  "verdict": "clean", "latency_ms": 1700 }
      },
      "delta": +1.5,
      "winner": "with_skill_v2"
    }
  ],
  "recommendation": {
    "winner": "with_skill_v2",
    "delta_mean_score": +4.9,
    "delta_pass_rate": +0.56,
    "cost_overhead_pct": +70.8,
    "verdict": "APPROVE",
    "summary": "Skill v1.3.0 improves mean score by 4.9 points (+153%) with 56pp higher pass rate. Cost increase of 70% is justified by quality gain. Recommend publishing."
  }
}
```

---

### 12.6 Side-by-Side Terminal Report

When `skillctl eval report report.json --format table` is run, the output is a rich terminal table:

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  EVAL REPORT — my-org/code-reviewer-eval@1.0.0                              ║
║  Run: run_01HXZ...   2026-03-21 10:00:00 UTC                                ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  SCENARIO              │ BASELINE │ SKILL v1.2 │ SKILL v1.3 │  DELTA (b→v2) ║
╠════════════════════════╪══════════╪════════════╪════════════╪═══════════════╣
║  sqli_basic            │  2.0 ✗   │   7.5 ✓    │   9.0 ✓    │  +7.0  🟢     ║
║  safe_code_no_fp       │  8.0 ✓   │   8.5 ✓    │   9.5 ✓    │  +1.5  🟢     ║
║  xss_react             │  1.5 ✗   │   6.0 ✓    │   7.8 ✓    │  +6.3  🟢     ║
╠════════════════════════╪══════════╪════════════╪════════════╪═══════════════╣
║  MEAN SCORE            │   3.2    │    7.3     │    8.7     │  +5.5  🟢     ║
║  PASS RATE             │  33.3%   │   88.9%    │   88.9%    │ +55.6pp       ║
║  AVG LATENCY           │  1200ms  │   1750ms   │   1850ms   │  +54%  🟡     ║
║  EST. COST / 100 runs  │  $0.024  │   $0.038   │   $0.041   │  +71%  🟡     ║
╠════════════════════════╪══════════╪════════════╪════════════╪═══════════════╣
║  VERDICT: ✅ APPROVE publish my-org/code-reviewer@1.3.0                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

### 12.7 Governance Integration

The eval suite integrates directly into the governance workflow:

1. **Eval-gated publishing:** Registry server can be configured to require a passing eval run before `skillctl publish` succeeds. Set `governance.requireEval: true` in `.skillctlrc`.

2. **Eval in CI:** The GitHub Action (Milestone 5) runs `skillctl eval run --ci` automatically on PR. A skill version with a failing eval cannot be merged to `main`.

3. **Eval history in audit log:** Every eval run is recorded in the audit log with run ID, variant scores, and verdict. Compliance auditors can prove that every published skill was evaluated.

4. **Regression protection:** `skillctl eval diff` between two runs detects score regression. If a new skill version scores lower than the previous published version on any scenario, the diff command exits non-zero and blocks CI.

5. **Eval results in registry metadata:** When a skill is published, its latest eval report is stored alongside it in the registry. Consumers can run `skillctl show my-org/code-reviewer@1.3.0 --eval` to see the evaluation results before adopting the skill.

---

### 12.8 Milestone 6 — Eval Suite (Week 16–18)

**Goal:** Eval suite fully integrated with CLI, registry, and CI pipeline

**Deliverables:**
- [ ] `evalsuit.yaml` schema + parser
- [ ] `skillctl eval run` — executes all variants and scenarios
- [ ] Deterministic judge engine (assert_contains, assert_not_contains, max_tokens, regex)
- [ ] LLM-as-judge engine (calls Anthropic API with user-defined rubric)
- [ ] Human judge queue (async, stored in registry server, resolved via `skillctl eval review`)
- [ ] `EvalReport` struct + `report.json` writer
- [ ] `skillctl eval report` — rich terminal table renderer (using `bubbletea` or `lipgloss`)
- [ ] `skillctl eval diff` — regression detection between two reports
- [ ] `skillctl eval history` — lists past runs from remote registry
- [ ] Registry server: stores eval reports alongside skill metadata
- [ ] Registry server: `governance.requireEval` enforcement on publish
- [ ] GitHub Action: runs eval in CI, blocks merge on failure
- [ ] 3 example eval suites in `/examples/`
- [ ] Eval runner: full trace capture (all LLM calls, tool invocations, token counts) stored as `trace_ref` in report

**Definition of Done:** `skillctl eval run` produces a valid report comparing baseline vs. skill on 3+ scenarios. `skillctl eval diff` correctly detects a regression when a scenario score drops. Registry rejects `skillctl publish` when `requireEval: true` and no passing run exists for that version.

---

### 12.9 Implementation Notes for Eval Suite

1. **The agent execution is real, not mocked.** Eval must call the actual LLM API (Anthropic, OpenAI, etc.) with the real skill content injected. Using stubs defeats the purpose.

2. **Skill injection mechanism:** For each variant with a skill, the skill content is prepended to the system prompt with a clear delimiter (`--- SKILL: my-org/code-reviewer@1.3.0 ---`). This is the same injection method the runtime uses in production — consistency is mandatory.

3. **LLM-as-judge model is configurable but defaults to `claude-sonnet-4-20250514`.** The judge model MUST be different from the agent model being evaluated — otherwise you get circular self-assessment. Add a validation warning if they are the same.

4. **Scenario IDs must be stable across versions** of the eval suite. Renaming a scenario ID breaks `skillctl eval diff` regression detection. Validate that all scenario IDs in a new eval suite version are either unchanged or explicitly marked as `replaced_by: new_id`.

5. **Cost tracking is a first-class output.** Every eval run records estimated cost in USD (input tokens × price + output tokens × price, per model's published pricing). This lets teams make informed cost/quality tradeoffs when comparing skill versions.

6. **Eval reports are content-addressed.** Store reports in the local registry under `~/.skillctl/evals/<sha256-of-report>/` — same pattern as skills. This prevents silent overwrite of historical eval data.

7. **Human judge queue timeout.** If `type: human` judge is `required: true` and no human review is submitted within `human_timeout` (default: 7 days), the run expires with verdict `EXPIRED`. This prevents blocking CI indefinitely on unreviewed evals.
