# skillctl Reference

Complete CLI reference, skill format specification, registry server setup, eval suite details, optimizer configuration, and API endpoints.

---

## Skill Format

Every skill is defined by a `skill.yaml` manifest and a `SKILL.md` instructions file.

### skill.yaml

```yaml
apiVersion: skillctl.io/v1
kind: Skill

metadata:
  name: my-org/code-reviewer
  version: 1.0.0
  description: "Reviews PRs for security issues and code quality"
  authors:
    - name: Alice
      email: alice@example.com
  license: MIT
  tags:
    - security
    - code-review
  category: code-review  # optional — see Known Categories below

spec:
  content:
    path: ./SKILL.md
  parameters:
    - name: strictness
      type: enum
      values: ["low", "medium", "high"]
      default: "medium"
  capabilities:
    - read_file
    - read_code
  dependencies:
    - name: my-org/base-engineering
      version: ">=1.0.0 <2.0.0"

governance:
  approvals:
    required: 1
    from: ["owner", "admin"]
  channels:
    - my-org/engineering
```

### Known Categories

The optional `metadata.category` field classifies a skill into a taxonomy. Custom values are allowed but produce a validation warning.

| Category | Description |
|----------|-------------|
| `code-review` | Code review and PR analysis |
| `data` | Data processing and pipelines |
| `deployment` | Deployment and release automation |
| `design` | Design systems and UI patterns |
| `dev-tools` | Developer tooling and productivity |
| `documentation` | Documentation generation and maintenance |
| `frameworks` | Framework-specific guidance |
| `general` | General-purpose skills |
| `infrastructure` | Infrastructure and IaC |
| `observability` | Logging, monitoring, and alerting |
| `security` | Security scanning and hardening |
| `testing` | Testing strategies and automation |

### Backward compatibility

Plain `SKILL.md` files (no `skill.yaml`) are auto-detected and wrapped in a minimal manifest with a warning. You don't need to rewrite existing skills to adopt governance.

---

## CLI Reference

All commands follow kubectl-style verb patterns: `skillctl <verb> [resource] [args] [flags]`

### Core commands

| Command | Description |
|---------|-------------|
| `skillctl configure` | Interactive setup wizard (registry backend, LLM model, budget) |
| `skillctl apply [path]` | Validate + security scan + push to local store; publish to remote if configured |
| `skillctl create skill <name>` | Scaffold a new skill (skill.yaml + SKILL.md) |
| `skillctl get skills` | List skills from local store (or remote with `--remote`) |
| `skillctl get skill <ref>` | Pull/show a specific skill by name@version |
| `skillctl describe skill <ref>` | Rich detail: metadata, versions, parameters, capabilities |
| `skillctl delete skill <ref>` | Remove a skill version from local store |
| `skillctl logs <name>` | Show audit trail for a skill (from registry) |
| `skillctl validate [path]` | Validate manifest structure, semver, capabilities |
| `skillctl diff <ref-a> <ref-b>` | Compare two skill versions with breaking change detection |
| `skillctl export` | Export skills from local store to a portable archive (tar.gz or zip) |
| `skillctl doctor` | Diagnose environment issues |
| `skillctl version` | Print version info |

### `apply` flags

| Flag | Description |
|------|-------------|
| `-f <path>` | Path to skill (alias for positional argument) |
| `--dry-run` | Preview without mutating state |
| `--local` | Skip remote publish, only push to local store |

### `export` flags

| Flag | Description |
|------|-------------|
| `--output <path>` / `-o` | Output file path (default: `skillctl-export-{timestamp}.tar.gz`) |
| `--format tar.gz\|zip` | Archive format (default: `tar.gz`) |
| `--namespace <ns>` | Export only skills in this namespace |
| `--tag <tag>` | Export only skills with this tag |

### Registry commands

| Command | Description |
|---------|-------------|
| `skillctl serve` | Start the self-hosted registry server |
| `skillctl token create` | Create an API token for registry access |
| `skillctl login` | Authenticate with GitHub via device flow |
| `skillctl logout` | Remove stored GitHub credentials |
| `skillctl config set <key> <value>` | Set a configuration value |
| `skillctl config get <key>` | Read a configuration value |

### Configuration keys

| Key | Description |
|-----|-------------|
| `registry.backend` | `local` or `agent-registry` |
| `registry.local.url` | Self-hosted registry URL |
| `registry.local.token` | Registry auth token |
| `registry.agent_registry.registry_id` | AWS Agent Registry ARN |
| `registry.agent_registry.region` | AWS region |
| `optimize.model` | LiteLLM model ID |
| `optimize.budget_usd` | Optimizer budget in USD |
| `github.token` | GitHub access token |
| `github.client_id` | GitHub OAuth App client ID |

Backward-compatible aliases: `registry.url` maps to `registry.local.url`, `registry.token` maps to `registry.local.token`.

### Backward-compatible aliases

| Old command | Maps to |
|-------------|---------|
| `skillctl init <name>` | `skillctl create skill <name>` |
| `skillctl push [path]` | `skillctl apply --local [path]` |
| `skillctl pull <ref>` | `skillctl get skill <ref>` |
| `skillctl list` | `skillctl get skills` |
| `skillctl publish [path]` | `skillctl apply [path]` |
| `skillctl search [query]` | `skillctl get skills --remote --query <query>` |

### Eval commands

| Command | Description |
|---------|-------------|
| `skillctl eval audit <path>` | Security and structure audit with A-F grading |
| `skillctl eval functional <path>` | Baseline comparison (with/without skill) |
| `skillctl eval trigger <path>` | Activation reliability testing |
| `skillctl eval report <path>` | Unified report (40% audit, 40% functional, 20% trigger) |
| `skillctl eval snapshot <path>` | Save current results as regression baseline |
| `skillctl eval regression <path>` | Detect score drops vs baseline |
| `skillctl eval compare <a> <b>` | Side-by-side skill comparison |
| `skillctl eval lifecycle <path>` | Version tracking and change detection |

### Install commands

| Command | Description |
|---------|-------------|
| `skillctl install <ref-or-path> --target <targets>` | Install a skill to AI coding IDEs |
| `skillctl uninstall <ref> --target <targets>` | Remove a skill from AI coding IDEs |
| `skillctl get installations` | List all skillctl-managed installations |

### `install` flags

| Flag | Description |
|------|-------------|
| `--target <targets>` | **Required.** Comma-separated IDE names or `all`. Valid: `claude`, `cursor`, `windsurf`, `copilot`, `kiro` |
| `--global` | Install to user-level directory instead of project-level |
| `--force` | Overwrite files modified since last install |
| `--dry-run` | Preview what would be installed without writing files |

### `get installations` flags

| Flag | Description |
|------|-------------|
| `--target <ide>` | Filter by IDE target |
| `--json` | Output as JSON |

### Supported IDE targets

| Target | Project path | Global path | Format |
|--------|-------------|-------------|--------|
| `claude` | `.claude/skills/{name}/SKILL.md` | `~/.claude/skills/{name}/SKILL.md` | Markdown + Claude frontmatter |
| `cursor` | `.cursor/rules/{name}.mdc` | — | Markdown + Cursor frontmatter |
| `windsurf` | `.windsurf/rules/{name}.md` | `~/.codeium/windsurf/memories/global_rules.md` | Markdown + Windsurf frontmatter |
| `copilot` | `.github/instructions/{name}.instructions.md` | — | Markdown + Copilot frontmatter |
| `kiro` | `.kiro/steering/{name}.md` | `~/.kiro/steering/{name}.md` | Markdown + Kiro frontmatter |

`--target all` auto-detects which IDEs are present by checking for their config directories. `--global` is only supported for targets with a global path (claude, windsurf, kiro).

Frontmatter is automatically translated to each IDE's native format. Fields that don't map (e.g., `allowed-tools` for Cursor) are dropped with a warning to stderr.

### Optimizer commands

| Command | Description |
|---------|-------------|
| `skillctl optimize [path]` | Run automated improvement loop |
| `skillctl optimize history` | List past optimization runs |
| `skillctl optimize diff <run-id>` | Show original vs promoted diff |

### Optimizer flags

| Flag | Default | Description |
|------|---------|-------------|
| `--variants` | 3 | Number of candidate variants per cycle |
| `--threshold` | 0.05 | Minimum improvement to promote (5%) |
| `--max-iterations` | 50 | Hard cap on optimization cycles |
| `--plateau` | 3 | Stop after N cycles with no improvement |
| `--budget` | 10.0 | Maximum spend in USD |
| `--timeout` | 120 | Evaluation timeout in seconds |
| `--agent` | claude | Agent to use for evaluation |
| `--model` | bedrock/us.anthropic.claude-opus-4-6-v1 | LiteLLM model ID (any provider) |
| `--approve` | false | Auto-approve promotions without confirmation |
| `--dry-run` | false | Run the loop without writing changes |

### Common flags

- `--json` — JSON output (available on validate, list, diff, and eval commands)
- `--dry-run` — Preview without mutating state (push, optimize)
- `--strict` — Treat warnings as errors (validate)
- `--verbose` / `-v` — Show additional detail (eval audit)

---

## Registry Server

The registry server is a self-hostable FastAPI application backed by SQLite and filesystem blob storage.

### Start with Docker

```bash
docker compose up
```

Starts the registry at `http://localhost:8080` with persistent data in `./registry-data/`.

### Start from CLI

```bash
skillctl serve --port 8080
```

Data is stored at `~/.skillctl/registry/` by default. Use `--data-dir` to override.

### Configure the CLI

```bash
skillctl configure                    # interactive wizard
# or manually:
skillctl config set registry.url http://localhost:8080
```

Environment variables: `SKILLCTL_REGISTRY_URL`, `SKILLCTL_REGISTRY_TOKEN`.

### Authentication

Permission-scoped tokens:

- `read` — read-only access to all skills
- `write:<namespace>` — publish/delete within a namespace
- `admin` — full access, including token management

```bash
skillctl token create --name ci-publisher --scope write:my-org --scope read
```

Use `--auth-disabled` for local development only.

### API endpoints

```
GET    /api/v1/health                              # Health check
GET    /api/v1/skills                              # List/search skills
GET    /api/v1/skills/{namespace}/{name}            # Skill detail (latest version)
GET    /api/v1/skills/{namespace}/{name}/{version}  # Specific version
GET    /api/v1/skills/{namespace}/{name}/{version}/content  # Download content
POST   /api/v1/skills                              # Publish skill
DELETE /api/v1/skills/{namespace}/{name}/{version}  # Delete version
PUT    /api/v1/skills/{namespace}/{name}/{version}/eval  # Attach eval grade
POST   /api/v1/tokens                              # Create token
DELETE /api/v1/tokens/{token_id}                    # Revoke token
```

### Audit log

Every mutating operation is logged to an append-only JSONL file signed with HMAC-SHA256.

### GitHub storage backend

The registry can use a GitHub repository as its backing store instead of local filesystem. See `skillctl serve --storage github` and configure via `skillctl configure` or `skillctl config set github.repo <url>`.

---

## Eval Suite Details

### Security audit categories

| Code | Category | What it detects |
|------|----------|----------------|
| SEC-001 | Secrets | API keys, tokens, passwords, AWS keys, private keys |
| SEC-002 | URLs | External URLs and data exfiltration surfaces |
| SEC-003 | Subprocess | Shell execution, subprocess calls |
| SEC-004 | Installs | curl\|bash, unpinned pip install |
| SEC-005 | Deserialization | pickle, marshal, yaml.load |
| SEC-006 | Dynamic imports | importlib, __import__, exec/eval |
| SEC-007 | Base64 | Encoded payloads |
| SEC-008 | MCP | MCP server references |
| SEC-009 | Injection | Prompt injection surfaces |

Grading: 100 - (25 x critical) - (10 x warning) - (2 x info), clamped to 0. A (90+), B (80+), C (70+), D (60+), F (<60).

### Eval configuration

Create `.skilleval.yaml` in your skill directory:

```yaml
ignore:
  - STR-017              # Suppress specific finding codes
safe_domains:
  - api.mycompany.com   # Treat as safe for URL scanning
min_score: 70            # Fail if score drops below this
severity_overrides:
  SEC-002: INFO          # Downgrade a finding severity
```

### Provenance

Every optimization run is stored at `~/.skillctl/optimize/<run-id>/` with: original content, each variant, failure analyses, eval reports, and promotion decisions.

---

## Examples

The `examples/` directory contains three skill examples:

| Example | Description |
|---------|-------------|
| `basic-skill/` | Complete skill with skill.yaml, SKILL.md, and sample eval report |
| `parameterized-skill/` | Typed parameters (string, enum, number, boolean) with governance |
| `minimal-skill/` | Plain SKILL.md with no manifest (tests backward compatibility) |

```bash
skillctl validate examples/basic-skill
skillctl eval audit examples/basic-skill
```

---

## Compatibility

### Python versions

| Python | Status |
|--------|--------|
| 3.10 | Supported (tested in CI) |
| 3.11 | Supported (not explicitly tested) |
| 3.12 | Supported (tested in CI) |
| 3.13 | Supported (tested in CI) |

### Optional dependencies

| Feature | Extra | Key dependencies |
|---------|-------|-----------------|
| Core CLI | (none) | pyyaml |
| Registry server | `[server]` | fastapi, uvicorn |
| Optimizer | `[optimize]` | litellm (>=1.83.14) |
| Claude Code plugin | `[plugin]` | mcp |
| All features | `[all]` | All of the above |
| Development | `[dev]` | pytest, hypothesis, httpx |

```bash
pip install skillctl              # core only
pip install "skillctl[optimize]"  # + optimizer
pip install "skillctl[all]"       # everything
```
