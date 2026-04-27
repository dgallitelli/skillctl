<p align="center">
  <img src="skillctl.svg" alt="skillctl logo" width="450" />
</p>

<p align="center">
  <em>What kubectl does for Kubernetes, skillctl does for agent skills.</em>
</p>

<p align="center">
  <a href="https://github.com/dgallitelli/skillctl/actions/workflows/ci.yml"><img src="https://github.com/dgallitelli/skillctl/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.10%20|%203.12%20|%203.13-blue" alt="Python">
  <img src="https://img.shields.io/badge/coverage-41%25-yellow" alt="Coverage">
  <img src="https://img.shields.io/badge/pyright-strict-green" alt="Type Checked">
  <img src="https://img.shields.io/badge/license-MPL--2.0-blue" alt="License">
  <img src="https://img.shields.io/badge/pip--audit-clean-green" alt="Security">
</p>

---

## The problem

As teams adopt AI agents, skills proliferate — code review skills, deployment skills, data analysis skills, each written by different people with different quality bars. Without governance, you get:

- **No visibility** — nobody knows what skills exist or who owns them
- **No quality gate** — skills with hardcoded secrets or prompt injection patterns reach production
- **No evaluation** — "does this skill actually help?" is answered by gut feeling, not data
- **No versioning** — breaking changes ship without warning

## What skillctl does

**skillctl** is an open-source governance platform for agent skills. One CLI to validate, evaluate, publish, and optimize skills across any agent runtime.

### Validate before it ships

```bash
skillctl validate ./my-skill          # schema, semver, capabilities
skillctl eval audit ./my-skill        # security scan → A-F grade
```

The security scanner checks for secrets, prompt injection, data exfiltration URLs, unsafe deserialization, encoded payloads, and more. Skills with critical findings are **blocked from publishing**.

### Evaluate with data, not gut feeling

```bash
skillctl eval functional ./my-skill   # runs agent with/without skill, measures difference
skillctl eval trigger ./my-skill      # does the skill fire when it should?
skillctl eval report ./my-skill       # unified score: 40% audit + 40% functional + 20% trigger
```

Every eval produces a grade (A-F) and a score (0-100). Regression detection catches quality drops between versions.

### Optimize automatically

```bash
skillctl optimize ./my-skill --budget 5.0
```

The optimizer runs an iterative loop: evaluate → identify weaknesses via LLM → generate improved variants → re-evaluate → promote the best. Works with any LLM provider via [LiteLLM](https://docs.litellm.ai/docs/providers) (Bedrock, OpenAI, Ollama, etc.).

### Publish with governance

```bash
skillctl apply ./my-skill             # validate + security scan + push to registry
```

`apply` is the governance gate. It validates the manifest, runs a security scan, pushes to a content-addressed local store, and optionally publishes to a remote registry — self-hosted or [AWS Agent Registry](https://aws.amazon.com/blogs/machine-learning/the-future-of-managing-agents-at-scale-aws-agent-registry-now-in-preview/).

Every mutation is versioned, diffable, and auditable.

---

## Quickstart

```bash
# Install
git clone https://github.com/dgallitelli/skillctl.git
cd skillctl
pip install .                         # core CLI (Python 3.10+)
pip install ".[optimize]"             # + optimizer (LiteLLM)
pip install ".[plugin]"              # + MCP server for Claude Code plugin

# Configure
skillctl configure                    # registry backend, LLM model, budget

# Create and govern a skill
skillctl create skill my-org/my-skill
skillctl validate
skillctl eval audit .
skillctl apply
```

See [docs/REFERENCE.md](docs/REFERENCE.md) for the full CLI reference, registry server setup, eval suite details, optimizer flags, skill format spec, and API endpoints.

### Claude Code plugin

skillctl ships a [Claude Code plugin](https://code.claude.com/docs/en/plugins) in the `plugin/` directory. It gives Claude direct access to skillctl operations via MCP tools and teaches it the skill governance workflow via skills.

```bash
# Test locally
claude --plugin-dir ./plugin

# Skills available:
#   /skillctl:skill-lifecycle   — full validate → eval → optimize → publish workflow
#   /skillctl:create-skill      — scaffold and author new skills
#   /skillctl:diagnose-skill    — interpret eval results and fix findings

# 13 MCP tools exposed:
#   skillctl_validate, skillctl_apply, skillctl_list, skillctl_describe,
#   skillctl_delete, skillctl_diff, skillctl_create, skillctl_eval_audit,
#   skillctl_eval_functional, skillctl_eval_trigger, skillctl_eval_report,
#   skillctl_optimize, skillctl_optimize_history
```

When running inside Claude Code, `skillctl` emits a plugin hint on stderr so Claude Code can prompt users to install the plugin automatically.

---

## Key features

| Feature | What it does |
|---------|-------------|
| **Security scanning** | 9 threat categories, ~50 pattern detectors, A-F grading |
| **Functional evaluation** | With/without-skill baseline comparison via LLM-as-judge |
| **Trigger evaluation** | Activation recall and specificity measurement |
| **Automated optimization** | LLM-driven iterative improvement loop with budget control |
| **Content-addressed storage** | SHA-256 hashing, integrity verification, structural diffing |
| **Self-hosted registry** | FastAPI + SQLite + FTS5 search, HMAC-signed audit logs |
| **AWS Agent Registry** | Native integration via `bedrock-agentcore-control` API |
| **Provider-agnostic LLM** | Any model via LiteLLM (Bedrock, OpenAI, Anthropic, Ollama, ...) |
| **Runtime-agnostic** | Works with Claude, GPT, Gemini, or any SKILL.md-based agent |
| **Claude Code plugin** | MCP tools + skills for governance inside agentic IDEs |

## How it fits in

```
Author writes skill
    → skillctl validate        (schema check)
    → skillctl eval audit      (security scan, A-F grade)
    → skillctl eval functional (behavioral testing)
    → skillctl optimize        (automated improvement)
    → skillctl apply           (push to store + publish to registry)
    → Enterprise discovery     (self-hosted registry or AWS Agent Registry)
```

---

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/REFERENCE.md](docs/REFERENCE.md) | Full CLI reference, skill format, registry server, eval suite, optimizer flags, API endpoints |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System overview, module map, data flow diagrams |
| [CHANGELOG.md](CHANGELOG.md) | Version history and release notes |

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,optimize]"
pytest -m "not integration"           # 310 unit tests
pytest -m integration                 # 10 real Bedrock tests (needs AWS creds)
```

## License

[MPL-2.0](https://www.mozilla.org/en-US/MPL/2.0/)
