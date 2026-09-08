# Contributing to SkillsOps

Thanks for your interest!  This is a beta-stage open-source project; small,
focused PRs are easier to review and merge than large refactors.

## Quick start

```bash
git clone https://github.com/dgallitelli/skillsops.git
cd skillsops
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server,plugin,observability]"

# Run deterministic tests, then real local end-to-end tests.
pytest tests/ -m "not integration" -q
pytest tests/e2e -q

# Lint, format check, and type check (all blocking in CI).
ruff check skillctl tests
ruff format --check skillctl tests
pyright skillctl
```

The extracted optimizer has a separate environment and suite under
`packages/skillsops-optimize/`. Core SkillsOps tests do not require AWS
credentials or external network calls.

## Project conventions

- **Errors**: use `SkillctlError(code, what, why, fix)`.  Bare `raise` of
  `ValueError`/`RuntimeError` is OK in low-level helpers; user-facing
  paths should always carry the four fields.
- **Optional dependencies** (`fastapi`, `uvicorn`, `litellm`): import them
  inside the feature that needs them, not at module top — the core CLI
  must stay importable with only `pyyaml` installed.
- **Config**: read/write through `skillctl.config.load_config` /
  `save_config`, never raw YAML.
- **Secrets**: any file containing a token/HMAC key must be written with
  `skillctl._secure.atomic_write_secret` so it is never world-readable.
- **Tests**: in `tests/test_<module>.py`.  Use `tmp_path`; no real
  network or real AWS calls outside of `@pytest.mark.integration`.
- **CLI**: kubectl-style verbs (`apply`, `create`, `get`, `describe`,
  `delete`, `diff`, `logs`).  Older verbs (`init`, `push`, `pull`,
  `list`, `publish`, `search`) are kept as aliases.

See [docs/0-architecture.md](docs/0-architecture.md) for the module map
and data flows.

## Sending a PR

1. Branch from `main`, name the branch after the change
   (`fix-x`, `feat-y`).
2. Keep the diff focused — one fix or one feature per PR.
3. Update `CHANGELOG.md` under `## Unreleased`.  If the change is
   user-facing, also touch the relevant section of `README.md` or
   `docs/`.
4. Make sure both test commands above, `ruff check`, `ruff format --check`,
   and `pyright skillctl` all pass locally.
5. Open the PR; describe what you changed and why.  Link the issue if
   one exists.

## Reporting bugs

Open a GitHub issue with:

- The `skillctl version` output.
- A minimal reproduction (1–2 commands).
- Expected vs actual behaviour.

For **security vulnerabilities**, follow the disclosure process in
[`SECURITY.md`](SECURITY.md) instead — please don't file a public issue.

## Licensing

By contributing, you agree that your contributions will be licensed under
the [Mozilla Public License 2.0](LICENSE), the same license as the rest
of the project.
