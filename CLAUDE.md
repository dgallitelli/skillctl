# CLAUDE.md — Claude Code Guide for skillctl

## What this project is

skillctl is a Python CLI + registry server for governing agent skills. Think kubectl for skills.

## Quick reference

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # core + test deps
pip install -e ".[dev,optimize]" # + boto3 for optimizer
pip install -e ".[dev,server]"   # + fastapi/uvicorn for registry

# Run tests
pytest                                    # all tests (229)
pytest tests/ --ignore=tests/test_github_backend.py  # skip slow git tests
pytest tests/test_api.py -x -v           # just API tests

# Key commands
skillctl create skill my-org/my-skill    # scaffold
skillctl validate                        # check manifest
skillctl apply                           # validate + push + publish
skillctl eval audit ./my-skill           # security scan
skillctl optimize ./my-skill --dry-run   # optimizer (needs AWS creds)
skillctl serve --auth-disabled           # start registry server
```

## Project structure

- `skillctl/cli.py` — CLI entry point, all command handlers
- `skillctl/store.py` — local content-addressed storage
- `skillctl/manifest.py` — skill.yaml parser
- `skillctl/validator.py` — schema validation
- `skillctl/diff.py` — version comparison
- `skillctl/registry/` — FastAPI registry server (API, auth, storage, audit)
- `skillctl/eval/` — evaluation suite (audit, functional, trigger, unified report)
- `skillctl/optimize/` — automated skill optimizer (LLM-driven)
- `skillctl/github_auth.py` — GitHub device flow login

## Conventions

- Errors use `SkillctlError(code, what, why, fix)` — always include all four fields
- CLI commands follow kubectl verbs: apply, create, get, describe, delete, diff, logs
- Old commands (init, push, pull, list, publish, search) are kept as aliases
- Tests go in `tests/test_<module>.py` — integration tests use real SQLite/filesystem, optimizer tests mock LLM calls
- Dependencies: core needs only pyyaml + python-multipart. Server/optimizer deps are optional groups.

## Branches

- `main` — CLI-first release (no web UI)
- `web-ui-feature` — full HTMX web UI (browse, publish, evaluate, optimize, settings, dark mode)

## What NOT to do

- Don't add web UI code to main — it lives on the web-ui-feature branch
- Don't make boto3/fastapi/uvicorn required deps — they're optional groups
- Don't use bare string errors — always use SkillctlError with what/why/fix
- Don't skip validation before storing — no unvalidated skills in the store
