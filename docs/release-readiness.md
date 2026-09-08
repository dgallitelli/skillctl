# Release Readiness — Stabilization Candidate

Date: 2026-09-08
Version: `0.1.0b8`
Branch: `codex/p0-stabilization`
Base: `origin/main@1ff29fd`

## Decision

**Pass for a beta release candidate.** The stable CLI, registry, artifact, and
RBAC paths passed local qualification. Merge or publication should still wait
for hosted CI to confirm its Python 3.10/3.12/3.13 matrix and Docker Compose
job.

The policy, observability, compliance, deployment, identity/ABAC,
lineage/forensics, federation, and generated CI surfaces remain experimental
or preview. They are not release-blocking enforcement claims.

## Qualification results

| Gate | Result |
|---|---|
| Core tests, excluding separately timed Git backend and E2E suites | 780 passed |
| Git storage backend | 10 passed |
| Local end-to-end suite with server/plugin/OTel extras | 39 passed |
| Extracted optimizer unit suite | 111 passed, 3 external-provider tests deselected |
| Registry migration, concurrency, recovery, and archive-adversarial coverage | Passed within the suites above |
| Ruff lint | Passed for the full checkout |
| Ruff format | 177 files clean |
| Production type check | `pyright skillctl/ --pythonversion 3.10`: 0 errors |
| Dependency audit | `pip-audit`: no known vulnerabilities after the CI-required pip upgrade |
| Dogfood security audit | Three shipped examples passed with grade A and zero warnings/critical findings |
| Package build | sdist and wheel built successfully |
| Package metadata | `twine check`: passed for both artifacts |
| Distribution contents | Wheel has 115 entries with framework data, CI templates, and artifact/migration modules; sdist has the separate Claude plugin bundle; neither contains bytecode/cache files |
| Non-editable wheel install | CLI version/help, registry server import, and MCP runtime import passed in a fresh Python 3.13 venv |
| Container | Rebuilt on Python 3.12, booted as `appuser`, API health `ok`, Docker healthcheck `healthy` |
| Compose | YAML structure validated locally; `docker compose config` is blocking in CI |

## Remaining risks and follow-ups

1. This host has Docker Engine but no Compose plugin. The file was
   structurally parsed here; the new hosted CI job is the authoritative Compose
   validation.
2. Local execution covered Python 3.13 and the Python 3.12 container. Python
   3.10 and 3.12 package tests rely on the blocking hosted matrix.
3. The Git backend suite uses local repositories; no live GitHub network,
   credentials, or conflict/retry environment was exercised.
4. External optimizer/provider tests were not run. The obsolete core Bedrock
   test was removed because that integration moved to the separate optimizer
   package.
5. The fresh E2E environment emits two upstream TestClient/AnyIO deprecation
   warnings. Tests pass, but dependency compatibility should be watched.
6. A broad, non-blocking Pyright scan of tests and examples reports existing
   annotation debt. The shipped `skillctl/` target enforced by CI is clean.
7. SQLite and the file audit chain remain a single-node/single-process
   operational design. Multi-worker deployments need external coordination for
   audit serialization and metadata ownership.

## Release controls now in CI

- Core tests run on Python 3.10, 3.12, and 3.13 without excluding Git backend
  coverage.
- The 39-test local E2E suite is blocking with all required optional
  dependencies installed.
- Publishing repeats tests, E2E, lint, format, type checking, package build,
  and metadata validation before PyPI trusted publishing.
- Build smoke validates wheel contents and installs the wheel in a fresh
  environment.
- Container smoke validates Compose, image build, registry boot, and health.
