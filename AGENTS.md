# AGENTS.md — Development Guide for skillctl

## What this project is

skillctl is a Python CLI + self-hostable registry server for governing agent skills. It validates, evaluates, publishes, audits, and optimizes skills across any agent runtime. Think kubectl for skills.

**Key documentation:**
- `README.md` — value proposition, quickstart, feature overview
- `docs/REFERENCE.md` — full CLI reference, skill format, registry server, eval suite, API endpoints, optimizer flags
- `ARCHITECTURE.md` — system overview, module map, data flow diagrams
- `CHANGELOG.md` — version history

## Quick reference

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # core + test deps
pip install -e ".[dev,optimize]" # + LiteLLM for optimizer
pip install -e ".[dev,server]"   # + fastapi/uvicorn for registry

# Run tests
pytest -m "not integration"              # unit tests (310)
pytest -m integration                    # real Bedrock tests (10, needs AWS creds)
pytest tests/ --ignore=tests/test_github_backend.py  # skip slow git tests

# Key commands
skillctl configure                       # interactive setup wizard
skillctl create skill my-org/my-skill    # scaffold
skillctl validate                        # check manifest
skillctl apply                           # validate + security scan + push + publish
skillctl eval audit ./my-skill           # security scan
skillctl optimize ./my-skill --dry-run   # optimizer
skillctl serve --auth-disabled           # start registry server
```

## Project structure

- `plugin/` — Claude Code plugin (skills + MCP server)
  - `plugin/.claude-plugin/plugin.json` — plugin manifest
  - `plugin/skills/` — skill-lifecycle, create-skill, diagnose-skill
  - `plugin/scripts/mcp_server.py` — MCP stdio server wrapping skillctl as a library
  - `plugin/.mcp.json` — MCP server wiring for Claude Code
- `skillctl/cli.py` — CLI entry point, all command handlers
- `skillctl/config.py` — centralized typed config (`SkillctlConfig`, `load_config`, `save_config`, `run_configure_wizard`)
- `skillctl/store.py` — local content-addressed storage
- `skillctl/manifest.py` — skill.yaml parser + `SkillManifest.to_dict()`
- `skillctl/validator.py` — schema validation
- `skillctl/diff.py` — version comparison
- `skillctl/utils.py` — shared utilities (`parse_ref`, `read_skill_name_*`)
- `skillctl/install.py` — multi-IDE installer (Claude Code, Cursor, Windsurf, Copilot, Kiro)
- `skillctl/errors.py` — `SkillctlError(code, what, why, fix)` base exception
- `skillctl/registry/` — FastAPI registry server (API, auth, storage, audit)
- `skillctl/eval/` — evaluation suite (audit, functional, trigger, unified report)
- `skillctl/optimize/` — automated skill optimizer (LLM-driven via LiteLLM)
- `skillctl/github_auth.py` — GitHub device flow login

## Conventions

- Errors use `SkillctlError(code, what, why, fix)` — always include all four fields. `EvalError` subclasses it.
- CLI commands follow kubectl verbs: apply, create, get, describe, delete, diff, logs
- Old commands (init, push, pull, list, publish, search) are kept as aliases
- Tests go in `tests/test_<module>.py` — integration tests use real SQLite/filesystem, Bedrock tests use `@pytest.mark.integration`
- Dependencies: core needs only pyyaml. Server/optimizer/plugin deps are optional groups.
- LLM calls use LiteLLM (provider-agnostic). Default model: `bedrock/us.anthropic.claude-opus-4-6-v1`.
- Config is centralized in `skillctl/config.py` with typed dataclasses. Use `load_config()`/`save_config()`, not raw YAML.

## Branches

- `main` — CLI-first release (no web UI)
- `web-ui-feature` — full HTMX web UI (browse, publish, evaluate, optimize, settings, dark mode)

## What NOT to do

- Don't add web UI code to main — it lives on the web-ui-feature branch
- Don't make fastapi/uvicorn/litellm required deps — they're optional groups
- Don't use bare string errors — always use SkillctlError with what/why/fix
- Don't skip validation before storing — no unvalidated skills in the store
- Don't use raw `_load_config()` dict access for new code — use `load_config()` from `skillctl.config`
- Don't put lazy imports for local modules inside function bodies — only defer optional deps (litellm, fastapi, uvicorn)
