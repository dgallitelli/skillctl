# Architecture

skillctl is a governance platform for agent skills. It validates, evaluates, publishes, audits, and enforces policy on skills across any agent runtime. The system has four layers: a CLI, a local store, a registry server, and an automated optimizer.

## System Overview

```
                                         +---------------------+
                                         |   Agent Runtimes    |
                                         | (Claude, GPT, etc.) |
                                         +----------+----------+
                                                    |
                                              uses skills
                                                    |
+---------------------------------------------------v---------------------------------------------------+
|                                          skillctl                                                      |
|                                                                                                        |
|   +-------------+     +-----------+     +---------------+     +-----------+     +------------------+   |
|   |   CLI       |---->| Manifest  |---->|  Validator     |---->| Local     |---->| Registry Server  |   |
|   | (cli.py)    |     | Loader    |     | (Schema rules) |     | Store     |     | (FastAPI)        |   |
|   |             |     +-----------+     +---------------+     | (SHA-256) |     |                  |   |
|   | apply       |                                              +-----------+     | API + Auth       |   |
|   | create      |     +-----------+     +---------------+                        | SQLite + FTS5    |   |
|   | get         |---->| Eval      |---->| Audit Suite    |     +-----------+     | Blob Storage     |   |
|   | validate    |     | Suite     |     | - Security     |     | GitHub    |     | Audit Logging    |   |
|   | eval        |     |           |     | - Structure    |     | Backend   |<--->| (JSONL + HMAC)   |   |
|   | optimize    |     |           |     | - Permissions  |     | (git sync)|     +------------------+   |
|   | serve       |     |           |---->| Functional     |     +-----------+                            |
|   | diff        |     |           |     | - Agent runner |                                              |
|   | doctor      |     |           |     | - LLM grading  |     +-----------+                            |
|   | login       |     |           |---->| Trigger        |     | Optimizer |                            |
|   +-------------+     |           |     | - Precision    |     | (LLM loop)|                            |
|                        |           |     | - Recall       |     |           |                            |
|                        +-----------+     +---------------+     | Analyze   |                            |
|                                                |                | Generate  |                            |
|                                                |   scores       | Evaluate  |                            |
|                                                +--------------->| Promote   |                            |
|                                                                 +-----------+                            |
+----------------------------------------------------------------------------------------------------------+
```

## Skill Lifecycle

A skill is defined by two files: `skill.yaml` (the governance manifest) and `SKILL.md` (the agent instructions). Every mutation flows through a governance gate.

```
Author writes skill          skillctl validate          skillctl apply
skill.yaml + SKILL.md  --->  Schema validation    --->  Local store (SHA-256)
                              Capability check           |
                                                         +--> Registry publish (optional)
                                                               |
                                                               v
                                                         skillctl eval audit
                                                         Security scan (A-F grade)
                                                               |
                                                               v
                                                         skillctl eval functional
                                                         LLM-graded test cases
                                                               |
                                                               v
                                                         skillctl optimize
                                                         Iterative LLM improvement
```

## Module Map

### Core (`skillctl/`)

| Module | Purpose |
|--------|---------|
| `cli.py` | Entry point. kubectl-style command dispatch (apply, create, get, delete, diff, validate, eval, optimize, serve, doctor, login). |
| `manifest.py` | Parses `skill.yaml` into `SkillManifest` dataclass. Auto-wraps plain `SKILL.md` files. |
| `validator.py` | Schema validation: apiVersion, semver, name format, parameter types, capability checking. |
| `store.py` | Content-addressed local storage under `~/.skillctl/store/`. SHA-256 hashing, atomic writes, integrity verification on pull. |
| `diff.py` | Structural diff between two stored skill versions. Detects breaking changes (removed params, capabilities). |
| `errors.py` | `SkillctlError(code, what, why, fix)` — all user-facing errors must use this format. `EvalError` subclasses it. |
| `utils.py` | Shared utilities: `parse_ref` (name@version parsing), `read_skill_name_from_manifest`, `read_skill_name_from_frontmatter`. |
| `github_auth.py` | GitHub OAuth device flow for `skillctl login`. |
| `version.py` | Single-source version constant. |

### Registry Server (`skillctl/registry/`)

Self-hostable FastAPI server. Start with `skillctl serve`.

| Module | Purpose |
|--------|---------|
| `server.py` | App factory. Wires DB, storage, auth, audit, and API router with lifespan management. |
| `api.py` | REST endpoints: publish, search (FTS5), download, delete, eval attachment, token management, health. |
| `db.py` | SQLite with WAL mode. Skills table + FTS5 virtual table + tokens table. Parameterized queries throughout. |
| `storage.py` | Content-addressed blob storage on filesystem. Atomic writes via temp-file-then-rename. Hash validation on read. |
| `auth.py` | Three modes: disabled, token (HMAC-SHA256), GitHub. RBAC with namespace-scoped permissions. |
| `audit.py` | Append-only JSONL audit log with HMAC signatures for tamper detection. |
| `github_backend.py` | Git-backed storage that syncs the registry to a GitHub repo for distributed deployments. |
| `config.py` | Environment-variable-based server configuration. |

### Eval Suite (`skillctl/eval/`)

Run with `skillctl eval <subcommand>`. Grades skills A-F.

```
skillctl eval audit ./my-skill        # Static security scan (no LLM needed)
skillctl eval functional ./my-skill   # Run test cases against agent runtime
skillctl eval trigger ./my-skill      # Measure activation precision/recall
skillctl eval report ./my-skill       # Unified report combining all three
```

| Module | Purpose |
|--------|---------|
| `cli.py` | Eval orchestration. Runs audit checks, applies `.skilleval.yaml` config, calculates score/grade. |
| `audit/security_scan.py` | 9 threat categories: secrets, URLs, subprocess, installs, deserialization, dynamic imports, base64, MCP, injection. |
| `audit/structure_check.py` | Validates skill completeness: frontmatter, headings, sections, documentation quality. |
| `audit/permission_analyzer.py` | Checks declared capabilities vs actual tool usage. Detects over-privilege. |
| `schemas.py` | `Finding`, `AuditReport`, `Severity`, `Category` — shared types for audit pipeline. |
| `functional.py` | Runs eval cases from `evals.json` against an agent runtime. Measures outcome, process, style, efficiency. |
| `grading.py` | Deterministic pattern matching + LLM-as-judge for assertion grading. |
| `trigger.py` | Tests skill activation: should-trigger queries (recall) and should-not-trigger queries (specificity). |
| `agent_runner.py` | Abstract runner protocol. Executes skills against any agent runtime. |
| `compare.py` | A/B comparison of two skill versions on identical test cases. |
| `regression.py` | Re-runs audits against baselines to detect score degradation. |
| `unified_report.py` | Aggregates audit + functional + trigger into a weighted composite score. |
| `cost.py` | Token cost estimation using model pricing tables. |
| `lifecycle.py` | Skill state machine: draft -> active -> deprecated -> archived. |
| `html_report.py` | Renders audit results as a standalone HTML document. |

### Optimizer (`skillctl/optimize/`)

Run with `skillctl optimize ./my-skill`. Uses Claude Opus on Amazon Bedrock.

```
                     +------------------+
                     | Initial Eval     |
                     +--------+---------+
                              |
                     +--------v---------+
              +----->| Failure Analysis |  (LLM identifies weaknesses)
              |      +--------+---------+
              |               |
              |      +--------v---------+
              |      | Variant Generation|  (LLM rewrites SKILL.md)
              |      +--------+---------+
              |               |
              |      +--------v---------+
              |      | Evaluate Variants |  (Full eval suite)
              |      +--------+---------+
              |               |
              |      +--------v---------+
              |      | Promotion Gate    |  (Score threshold check)
              |      +--------+---------+
              |           |         |
              |        promote    reject
              |           |         |
              |           v         |
              |      Write SKILL.md |
              |                     |
              +---------------------+
                    (next cycle)

   Terminates on: convergence, budget exhaustion, or plateau detection
```

| Module | Purpose |
|--------|---------|
| `loop.py` | Core optimization loop. Orchestrates analyze -> generate -> eval -> promote cycles. |
| `llm_client.py` | `AnthropicBedrock` SDK client. Default model: `us.anthropic.claude-opus-4-6-v1`. Exponential backoff retries. |
| `types.py` | `OptimizeConfig`, `Variant`, `CycleRecord`, `FailureAnalysis`, `PromotionDecision`, `ProvenanceEntry`. |
| `variant_generator.py` | Prompts the LLM to rewrite SKILL.md targeting specific weaknesses. Round-robin weakness assignment. |
| `failure_analyzer.py` | Prompts the LLM to identify root causes from eval results. |
| `promotion_gate.py` | Threshold-based gate: variant score must exceed current best + threshold. |
| `budget.py` | Token spend tracking with configurable USD limits. |
| `eval_runner.py` | Bridge to the eval suite. Swaps SKILL.md, runs unified report, parses results. |
| `provenance.py` | Full audit trail of optimization runs in JSONL under `~/.skillctl/optimize/`. |
| `cli.py` | CLI handler for `optimize`, `optimize history`, `optimize diff`. |

## Data Flow

### `skillctl apply`

```
skill.yaml + SKILL.md
    |
    v
ManifestLoader.load()          Parse YAML into SkillManifest dataclass
    |
    v
SchemaValidator.validate()     Check apiVersion, semver, name, params, capabilities
    |
    v
ManifestLoader.resolve_content()   Read SKILL.md content
    |
    v
ContentStore.push()            SHA-256 hash -> ~/.skillctl/store/<prefix>/<hash>
    |                          Atomic write (tempfile + os.replace)
    |                          Index update (index.json)
    v
scan_security()                Security gate (only for remote publish)
    |                          CRITICAL findings -> block publish
    v
_publish_to_registry()         POST /api/v1/skills (multipart: manifest + content)
    |                          (optional, only if registry URL configured)
    v
Registry API                   Validate -> Store blob -> Insert SQLite -> Audit log
```

### `skillctl eval audit`

```
Skill directory
    |
    +----> structure_check.py    Frontmatter, headings, sections, naming
    |
    +----> security_scan.py      Secrets, URLs, subprocess, deserialization, ...
    |                            (9 pattern categories, ~50 regex patterns)
    +----> permission_analyzer.py  Declared vs actual capabilities
    |
    v
apply_config()                 .skilleval.yaml: ignore codes, severity overrides
    |
    v
calculate_score()              100 - (25 * critical) - (10 * warning) - (2 * info)
    |
    v
calculate_grade()              A (90+), B (80+), C (70+), D (60+), F (<60)
```

## Infrastructure

```
docker-compose.yml
    |
    v
Dockerfile                     Python 3.12-slim, non-root user, port 8000
    |
    v
uvicorn                        ASGI server running the FastAPI app
    |
    v
skillctl serve                 Equivalent to: uvicorn skillctl.registry.server:create_app
```

## Configuration

| Source | Purpose |
|--------|---------|
| `~/.skillctl/config.yaml` | CLI config: registry URL, GitHub token, client ID. Written with 0600 permissions. |
| `~/.skillctl/store/` | Local content-addressed skill store. |
| `~/.skillctl/index.json` | Store index mapping name@version to content hashes. |
| `~/.skillctl/optimize/` | Optimization run provenance logs. |
| `.skilleval.yaml` | Per-skill eval config: ignore codes, severity overrides, safe domains. |
| `SKILLCTL_REGISTRY_URL` | Environment variable override for registry URL. |
| `SKILLCTL_GITHUB_TOKEN` | Environment variable override for GitHub token. |

## LLM Provider

All LLM calls use **Amazon Bedrock** via the `anthropic.AnthropicBedrock` SDK. The default model is `us.anthropic.claude-opus-4-6-v1` (cross-region inference profile). Authentication uses the standard AWS credential chain.

There is no direct Anthropic API path — Bedrock is the only supported provider.
