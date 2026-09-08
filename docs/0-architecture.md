# Architecture

SkillsOps is a governance platform for agent skills. It validates, evaluates,
packages, publishes, audits, and installs complete skill artifacts. The core
system has three layers: the CLI, a local content-addressed store, and a
self-hosted registry. The LLM-driven optimizer is a separate distribution.

## System Overview

```
skill source directory
        |
        v
ManifestLoader -> SchemaValidator -> deterministic artifact builder
                                          |
                         +----------------+----------------+
                         |                                 |
                         v                                 v
              local content store                 security audit gate
              content + artifact                         |
              SHA-256 identities                         v
                                               registry create/publish
                                                         |
                                      +------------------+------------------+
                                      |                  |                  |
                                      v                  v                  v
                                 SQLite/FTS5       blob or Git store   HMAC audit log
                                      |
                                      v
                         RBAC + immutable namespace
```

## Skill Lifecycle

A skill is defined by two files: `skill.yaml` (the governance manifest) and `SKILL.md` (the agent instructions). Every mutation flows through a governance gate.

```
Author writes complete skill     skillctl validate       skillctl apply
manifest + instructions +  --->  schema/capability  ---> immutable bundle
scripts/references/assets        checks                   + local store
                                                             |
                                                             +--> security gate
                                                                  + remote draft
                                                                  + publish transition
```

## Module Map

### Core (`skillctl/`)

| Module | Purpose |
|--------|---------|
| `cli.py` | Entry point. kubectl-style command dispatch for lifecycle, registry, evaluation, policy, and administration commands. |
| `artifact.py` | Deterministic ZIP artifact contract, per-file hashes/modes, content binding, safe verification and extraction. |
| `install.py` | Multi-IDE skill installation, frontmatter translation, support-file preservation, and installation tracking via `~/.skillctl/installations.json`. |
| `manifest.py` | Parses `skill.yaml` into `SkillManifest` dataclass. Auto-wraps plain `SKILL.md` files. |
| `validator.py` | Schema validation: apiVersion, semver, name format, parameter types, capability checking. |
| `store.py` | Content-addressed local storage under `~/.skillctl/store/`. Stores legacy primary content plus complete immutable artifacts, with atomic writes, verification, export, and import. |
| `diff.py` | Structural diff between two stored skill versions. Detects breaking changes (removed params, capabilities). |
| `config.py` | Centralized typed config for registry, compatibility optimizer settings, and GitHub authentication. |
| `errors.py` | `SkillctlError(code, what, why, fix)` — all user-facing errors must use this format. `EvalError` subclasses it. |
| `utils.py` | Shared utilities: `parse_ref` (name@version parsing), `read_skill_name_from_manifest`, `read_skill_name_from_frontmatter`. |
| `github_auth.py` | GitHub OAuth device flow for `skillctl login`. |
| `version.py` | Single-source version constant. |

### Registry Server (`skillctl/registry/`)

Self-hostable FastAPI server. Start with `skillctl serve`.

| Module | Purpose |
|--------|---------|
| `server.py` | App factory. Wires DB, storage, auth, audit, and API router with lifespan management. |
| `api.py` | REST endpoints for draft creation, publish/unpublish, search, content/artifact download, delete, eval attachment, auth/RBAC, and health. |
| `db.py` | SQLite with WAL mode, bounded writer waits, and atomic lifecycle compare-and-set. Skills, FTS5, token, identity, namespace, and authorization metadata. |
| `migrations.py` | Ordered, transactional SQLite migrations shared by registry and RBAC persistence. Upgrade state is recorded in `schema_migrations`. |
| `storage.py` | Content-addressed blob storage on filesystem. Atomic writes and corruption repair, hash validation on read, and non-destructive consistency inventory at startup. |
| `auth.py` | Legacy-token compatibility and hierarchical permission validation. All decisions flow through RBAC middleware. |
| `audit.py` | Append-only JSONL audit log with HMAC signatures for tamper detection. |
| `github_backend.py` | Git-backed storage that syncs the registry to a GitHub repo for distributed deployments. |
| `config.py` | Environment-variable-based server configuration. |

### Eval Suite (`skillctl/eval/`)

Run with `skillctl eval <subcommand>`. Grades skills A-F.

```
skillctl eval audit ./my-skill        # Static security/quality audit
skillctl eval report ./my-skill       # 80% audit + 20% schema contract
skillctl eval snapshot ./my-skill     # Save a deterministic baseline
skillctl eval regression ./my-skill   # Compare with a saved baseline
```

| Module | Purpose |
|--------|---------|
| `cli.py` | Eval orchestration. Runs audit checks, applies `.skilleval.yaml` config, calculates score/grade. |
| `audit/security_scan.py` | 9 threat categories: secrets, URLs, subprocess, installs, deserialization, dynamic imports, base64, MCP, injection. |
| `audit/structure_check.py` | Validates skill completeness: frontmatter, headings, sections, documentation quality. |
| `audit/permission_analyzer.py` | Checks declared capabilities vs actual tool usage. Detects over-privilege. |
| `schemas.py` | `Finding`, `AuditReport`, `Severity`, `Category` — shared types for audit pipeline. |
| `contract.py` | Deterministic manifest and schema-contract scoring. |
| `regression.py` | Re-runs audits against baselines to detect score degradation. |
| `unified_report.py` | Fail-closed aggregation of audit and schema-contract results. |
| `cost.py` | Token cost estimation using model pricing tables. |
| `lifecycle.py` | Skill state machine: draft -> active -> deprecated -> archived. |
| `html_report.py` | Renders audit results as a standalone HTML document. |

### Experimental integration libraries

The policy/observability, compliance, deployment, identity/ABAC,
lineage/forensics, federation, and CI-template modules are composable previews,
not part of the registry's enforcement path. In particular:

- policy and telemetry run only when a host explicitly uses
  `SkillInterceptor`;
- deployment commands update a local state model and do not route live traffic;
- compliance output is a non-certifying mapping preview and cannot authorize
  promotion;
- identity utilities are not registry authentication;
- lineage and forensic results contain only caller-supplied records; and
- compliance-gated federation fails closed until a trusted verifier exists.

See the corresponding documents for each exact trust boundary.

### Optimizer (`packages/skillsops-optimize/`)

The LiteLLM-driven authoring optimizer is a separate package and command. It
depends on the deterministic core evaluation APIs but is not part of the
registry's governance decision path. See [4-optimization.md](4-optimization.md).

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
build_artifact()               Canonical ZIP + per-file SHA-256 inventory
    |
    v
ContentStore.push()            Content hash + artifact hash
    |                          Atomic writes + index update
    v
scan_security()                Security gate (only for remote publish)
    |                          CRITICAL findings -> block publish
    v
_publish_to_registry()         POST /api/v1/skills
    |                          multipart: manifest + content + artifact
    v
publish transition             POST /api/v1/skills/publish
    |                          (optional, only if registry URL configured)
    v
Registry API                   Verify bundle -> store both blobs -> SQLite -> audit
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
Dockerfile                     Python 3.12-slim, non-root user, port 8080
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
| `~/.skillctl/config.yaml` | Typed config managed by `skillctl configure`. Registry, compatibility optimizer, and GitHub settings. Written with 0600 permissions. |
| `~/.skillctl/store/` | Local content-addressed skill store. |
| `~/.skillctl/index.json` | Store index mapping name@version to content hashes. |
| `.skilleval.yaml` | Per-skill eval config: ignore codes, severity overrides, safe domains. |
| `SKILLCTL_REGISTRY_URL` | Environment variable override for local registry URL. |
| `SKILLCTL_REGISTRY_TOKEN` | Environment variable override for local registry token. |
| `SKILLCTL_GITHUB_TOKEN` | Environment variable override for GitHub token. |

## Claude Code Plugin (`plugin/`)

The `plugin/` directory is a
[Claude Code plugin](https://code.claude.com/docs/en/plugins) that exposes the
five core governance operations through MCP.

```
plugin/
├── .claude-plugin/
│   └── plugin.json              Plugin manifest (name, version, description)
├── scripts/
│   ├── launch_mcp.sh            Locates Python and launches the server
│   └── mcp_server.py            Five-tool MCP stdio server
└── .mcp.json                    Wires the MCP server for Claude Code discovery
```

| Component | Purpose |
|-----------|---------|
| **MCP server** | Exposes `validate`, `audit`, `bump`, `diff`, and `publish`. Calls skillctl as a library with structured JSON I/O. |
| **Plugin hint** | `skillctl` CLI emits a `<claude-code-hint>` on stderr when `CLAUDECODE=1`, prompting Claude Code users to install the plugin. |

### MCP Server Architecture

The MCP server (`plugin/scripts/mcp_server.py`) imports skillctl modules directly:

```
MCP stdio transport
    |
    v
FastMCP (mcp SDK)
    |
    v
Tool handlers
    |
    +----> ManifestLoader, SchemaValidator     (validate)
    +----> run_audit                           (audit)
    +----> semver file update                  (bump)
    +----> ContentStore, diff_skills           (diff)
    +----> apply_skill                         (publish)
```

All tools return structured JSON. Errors use the standard `SkillctlError(code, what, why, fix)` format.

## LLM Provider Boundary

The SkillsOps core and registry make no LLM calls. Provider-dependent
authoring logic lives in `packages/skillsops-optimize/`, which uses LiteLLM
and owns its provider credentials, costs, and integration tests separately.
