# Changelog

## Unreleased

### Milestone 4 — Enterprise Scale & Ecosystem

- **Identity federation** (`skillctl/identity/`): dependency-free OIDC (HS256
  JWT) validation, group→role mapping, TTL cache, and `to_rbac_identity` so
  federated users flow through the M1 RBAC engine. CLI: `skillctl identity inspect`.
- **ABAC** (`skillctl/abac/`): attribute-based permit/deny policy engine
  (subject/resource/action/environment), explicit-deny-wins, safe operator set,
  pluggable into the M2 interceptor via `ABACPolicyHook`.
- **Data lineage** (`skillctl/lineage/`): SQLite read/write tracking with
  transitive `trace_provenance`, `downstream_consumers`, and `who_accessed`.
- **Incident forensics** (`skillctl/forensics/`): investigative queries over
  lineage + audit. CLI: `skillctl forensics {provenance,who-accessed,invocations}`.
- **Multi-registry federation** (`skillctl/federation/`): `promote_skill` across
  registries with RBAC role gate + compliance gate (dev → staging → prod).
- **CI/CD templates** (`skillctl/cicd/`): GitHub Actions, GitLab CI, Jenkins
  governance pipelines. CLI: `skillctl ci {list,init}`.
- **Tests**: 42 unit tests (identity/ABAC/lineage) + 5 e2e
  (`tests/e2e/test_milestone_4.py`) with real JWTs, real ABAC through the
  interceptor, real lineage, and in-process RBAC registries for federation.
  See [docs/enterprise.md](docs/enterprise.md).

### Milestone 3 — Compliance Mapping & Progressive Deployment

**Part A — Compliance** (`skillctl/compliance/`):
- Framework model (Framework → Category → Requirement → Control) loaded from
  YAML; ships EU AI Act, ISO/IEC 42001, and NIST AI RMF. Add a framework with a
  YAML file, no code change.
- Deterministic `EvidenceCollector` gathers evidence from existing sources
  (security scan, HMAC audit log, RBAC, policy decisions, metadata, deployment
  records, attestations); each record carries a SHA-256 integrity hash.
- `RiskClassifier` (EU AI Act Annex III) classifies skills; `AttestationStore`
  records time-bounded, version-scoped human sign-offs.
- `ComplianceReportGenerator` produces JSON/Markdown reports with per-control
  status, score, gaps, and recommendations.
- CLI: `skillctl compliance {frameworks,classify,report,gaps,attest}`.

**Part B — Progressive Deployment** (`skillctl/deployment/`):
- `DeploymentEngine` with canary, blue-green, staged, and immediate strategies;
  `TrafficRouter` does consistent-hash routing; `HealthMonitor` computes
  error/denial/success rates and p99 latency with caller-driven (deterministic)
  health checks and `auto_rollback`.
- Every deployment transition is recorded in the HMAC audit chain. Deployment
  records feed the compliance `deployment` evidence type (EU AI Act Art 14-1-b).
- CLI: `skillctl deploy {canary,blue-green,staged,status,promote,rollback,history}`.

- **Tests**: 22 compliance/deployment unit tests + 8 e2e
  (`tests/e2e/test_milestone_3.py`) using real frameworks, real evidence,
  real deployment engine + router + health monitor. See
  [docs/compliance.md](docs/compliance.md) and [docs/deployment.md](docs/deployment.md).

### Milestone 2 — Runtime Policy Enforcement & Observability

Closes the runtime governance gap: skills are governed not just at publish time
but on every invocation.

- **Policy hook pipeline** (`skillctl/policy/`): pre/post hooks returning
  structured decisions (ALLOW/DENY/WARN/REDACT). The `PolicyEngine` short-circuits
  on the first DENY and chains REDACT modifications; the `SkillInterceptor`
  wraps execution with policy evaluation, timing, tracing, and audit.
- **Five built-in hooks**: rate-limit (SQLite sliding window), data-boundary,
  time-window, pii-redaction, output-size — all pure-Python, no extra deps.
- **External engines**: `OPAHook` (real HTTP to an OPA server, fail-open/closed)
  and `CedarHook` (local `cedarpy`, graceful degradation).
- **OpenTelemetry** (`skillctl/observability/`): `skill.invoke` spans with policy
  events; no-op tracer when OTel isn't installed. Structured JSON logging
  (stdlib, standalone).
- **Audit integration**: every policy decision is a tamper-evident
  `policy_decision` event in the HMAC chain (`make_audit_callback`).
- **CLI**: `skillctl policy {list,validate,test,history}` and
  `skillctl observe {status,test}`; config via `.skillctl/policies.yaml`.
- **New extras**: `[observability]`, `[policy-opa]`, `[policy-cedar]`.
- **Tests**: 20 policy-core unit tests + 6 e2e (`tests/e2e/test_milestone_2.py`)
  using real SQLite counters, real HMAC audit, an in-memory OTel collector, and
  a real in-process OPA server. See [docs/runtime-policy.md](docs/runtime-policy.md).

### Milestone 1 — Core Governance MVP (RBAC)

Adds role-based access control to the registry. Every authorization decision is
auditable, and the decision path is unified across new and legacy principals.

- **Permission model** (`skillctl/registry/rbac/`): 15 fine-grained permissions,
  4 nested roles (viewer < author < publisher < admin), and hierarchical,
  inherited namespaces (`org/acme` covers `org/acme/team-ml/...`).
- **Single decision engine** (`RBACEngine.check`): pure, deterministic, with a
  token-scope gate (scoped tokens only ever narrow privileges) and a
  human-readable reason on every decision.
- **SQLite store**: `users`, `role_assignments`, `namespaces`, `auth_decisions`
  tables sharing the registry DB; the `tokens` table gains `user_id`/`scopes`
  via idempotent migration. Passwords hashed with stdlib PBKDF2-HMAC-SHA256
  (no new dependency).
- **Dual audit**: every decision is written to the `auth_decisions` table AND
  the HMAC hash-chained log (`auth_decision` events); mutating events now carry
  `actor` and `token_id`.
- **Create/publish split**: `POST /skills` creates a draft (`skill:create`);
  `POST /skills/publish` publishes (`skill:publish`). New `skill.created` /
  `skill.published` / `skill.unpublished` audit actions.
- **Auth + admin endpoints**: `/auth/login`, `/auth/whoami`,
  `/auth/change-password`, scoped `/auth/tokens`, `/users`, `/rbac/assign|revoke|
  assignments|check`, `/namespaces`.
- **CLI**: `skillctl auth` (login/logout/whoami/change-password/token),
  `skillctl rbac` (assign/revoke/list/check), `skillctl namespace`
  (create/list/grant). Credentials stored at `~/.skillctl/credentials.json`
  (mode 0600).
- **First-run bootstrap**: an empty database provisions an initial `admin` and
  prints its credentials once.
- **Backward compatible**: `--auth-disabled` still works (actor `anonymous`);
  legacy permission-string tokens are bridged into role assignments; a missing
  token under enabled auth returns 401.
- **Tests**: 29 RBAC core unit tests + 13 end-to-end tests
  (`tests/e2e/test_milestone_1.py`) using a real server subprocess, real SQLite,
  real tokens, and real HMAC verification. See [docs/rbac.md](docs/rbac.md).

### Milestone 0 — Cleanup & Simplify (breaking)

The guiding principle is **Minimum Viable Governance**: keep only the
governance controls that are deterministic, auditable, and reproducible.

- **Removed LLM-as-judge evaluation.** `skillctl eval functional`,
  `skillctl eval trigger`, and `skillctl eval compare` are gone, along
  with the agent runner and grading modules. Non-deterministic scoring
  is unsuitable for governance decisions.
- **New deterministic scoring.** `skillctl eval report` now produces
  `80% security audit + 20% schema contract`. Both inputs are pure
  functions of the skill's files — the same skill always scores the
  same. Added `skillctl/eval/contract.py` with deterministic checks
  (manifest parseable, required fields, valid semver, capabilities is a
  list, no conflicting metadata).
- **Extracted the optimizer.** The LiteLLM-based optimizer moved to a
  separate package, `packages/skillsops-optimize/` (`skillsops-optimize`
  on PyPI). It is no longer importable as `skillctl.optimize`, the
  `skillctl optimize` command is removed, and `litellm` is no longer a
  core dependency. The `[optimize]` extra was removed.
- **Trimmed the MCP plugin from 14 tools to 5.** The plugin now exposes
  exactly `validate`, `audit`, `bump`, `diff`, `publish`. The 3 bundled
  authoring skills were removed; scaffolding/browsing remain in the CLI.
- **Added end-to-end tests** (`tests/e2e/`, marked `integration`) that
  exercise the real filesystem, the real registry server, and real
  crypto (SHA-256 content addressing, HMAC audit chain) — no mocks.

### Added
- `eval audit` now runs 19 authoring-quality checks (`QLT-001` … `QLT-019`)
  ported from [`dgallitelli/skill-reviewer`](https://github.com/dgallitelli/skill-reviewer).
  Covers description quality, reference-file structure, body content,
  bundled scripts, `allowed-tools` syntax, and MCP tool qualification.
  All QLT findings are advisory (INFO/WARNING) — they don't block `apply`.
- Every audit `Finding` now carries a `citation` field naming the rule's
  upstream source. Visible in text, JSON, and GitHub Actions report output.
- `diagnose-skill` plugin skill now documents the `QLT-*` finding family.

## v0.1.0b6 (2026-05-13)

A focused follow-up to v0.1.0b5 closing both alias gaps in the
strict-mode AST scanner — `from X import Y` and `import X as Y` now
resolve through file-wide alias maps so the scanner catches the same
dangerous calls regardless of how the user imported the module.  Adds
`skillctl validate --format=github` so schema/semver/capability errors
also render as inline PR annotations.

### Added

- **`skillctl validate --format=github`** — emits GitHub Actions
  workflow commands for schema/semver/capability errors and warnings,
  mirroring `eval audit --format=github`.  Together the two cover the
  full pre-publish gate as inline PR annotations.  Per-issue file
  routing: `load_warnings` (frontmatter-parsing issues) bind to
  `SKILL.md`; schema errors / warnings / capability warnings bind to
  `skill.yaml` (falling back to `SKILL.md` if `skill.yaml` is absent).
  Catastrophic load failures (unparseable YAML — the most CI-relevant
  scenario for an annotation) emit a single `::error
  file=skill.yaml,title=VAL-LOAD::` annotation instead of a Python
  traceback.  `--json` is kept as a backward-compatible alias for
  `--format=json`; passing both lets `--format` win.

- **Strict-mode AST scanner now resolves `import X as Y` module
  aliases.**  `import pickle as p; p.loads(data)` — and the multi-dot
  form `import xml.etree.ElementTree as ET` — now resolve through a
  parallel alias map symmetric to PR #12's `from`-import handling.
  The leading segment of an attribute-chain call site is rewritten:
  `p.loads` → `pickle.loads` so the dispatch table key matches.  Same
  treatment for `import yaml as y` (with `y.load(...)` and
  `y.load(..., Loader=...)` variants), `import subprocess as sp`
  (with `shell=True`), `import os as o`, and `import base64 as b`
  (with literal-concat).  Plain `import pickle; pickle.loads(x)` is
  unchanged — the chain was already canonical.  Closes the last
  honestly-disclosed alias gap from `docs/3-security-audit.md`.

  Mixed shapes (e.g. `from pickle import loads` AND `import marshal
  as m` in the same file) flag both; the alias maps are independent.
  Multi-dot module paths (`import xml.etree.ElementTree as ET`) are
  handled.  The asname-shadows-stdlib case (`import pickle as os;
  os.system(x)`) is the new honestly-disclosed gap — silent miss
  because the rewrite turns `os.system` into `pickle.system`,
  removing the un-aliased reading.

- **Strict-mode AST scanner now resolves `from`-import aliases.**
  `from pickle import loads; loads(data)` — and the `as`-rename form
  `from pickle import loads as P; P(data)` — now fire the same
  `SEC-006-AST` finding as the bare `pickle.loads(data)` call.  Same
  treatment for `from yaml import load` (without `Loader=`),
  `from subprocess import run` (with `shell=True`), `from os import
  system` / `popen`, and `from base64 import b64decode` (with
  literal-concat).  Closes a gap that was previously documented as NOT
  closed in `docs/3-security-audit.md`.  The alias map is collected
  via `ast.walk` and is therefore file-wide by design — `from`-imports
  inside function bodies, conditionals, and `try/except` blocks
  contribute alongside module-scope imports.  Relative imports
  (`from .pickle import loads`) are deliberately skipped so the
  scanner doesn't false-positive on local submodules that happen to
  share a stdlib name.  See the new "File-wide alias map (deliberate
  trade-off)" section in `docs/3-security-audit.md` for the rationale.
  `import X as Y` module aliasing (`import pickle as p; p.loads(x)`)
  remains an honestly-disclosed gap.

  Symmetric tightening: the same alias-map refactor removes the old
  bare-name `b64decode` fallback.  `b64decode("AA" + "BB" + ...)` is
  now flagged only when `b64decode` resolves to `base64.b64decode`
  (via attribute access or `from base64 import b64decode`); a
  bare-name call against a hand-rolled local `def b64decode(x)` with
  no matching import is no longer flagged.  Sufficiently-long
  single-literal payloads (`b64decode("…lots of base64…")`) continue
  to be caught by the line-oriented `LONG_BASE64_STRING` regex; the
  AST's job remains the multi-literal-concat bypass.

### Changed

- `_output_is_machine` now also returns True for `--format=<non-text>`
  values (was: only `--json` / `--quiet` / non-TTY).  Affects
  breadcrumb suppression in `validate` and `eval audit` so future
  formats don't have to opt in individually.

## v0.1.0b5 (2026-05-12)

A capability-and-cleanup release on top of the v0.1.0b4 security
hardening.  Two new audit features (`--strict` AST pass, `--format=github`
PR annotations), an internal refactor that turns the apply lifecycle
into a real library function, and several smaller polish items.

### Added

- **`skillctl eval audit --strict`** — bypass-resistant audit mode
  that addresses several specific gaps in the default audit's
  coverage.  Opt-in; the default audit is unchanged.
  **Closed:** multi-line `eval`/`exec`/`compile`/`__import__` (Python
  AST pass — emits `SEC-007-AST`); `pickle.loads` / `marshal.loads` /
  `shelve.open` / `yaml.load` without `Loader=` (`SEC-006-AST`);
  `subprocess.* shell=True` and `os.system` / `os.popen`
  (`SEC-003-AST`); base64 literal-concatenation
  (`b64decode("AA" + "BB" + ...)` — `SEC-008-AST`); fullwidth and
  mathematical-alphanumeric homoglyphs (NFKC normalisation before
  regex matching — `ｅｖａｌ` and `𝐞𝐯𝐚𝐥` now match the existing
  `eval` pattern); files between 1 MB and 10 MB (default cap raised
  in strict mode, with STR-022 INFO emitted for any file still over
  cap so operators see the audit was incomplete instead of silently
  truncating).  **Honestly NOT closed:** Cyrillic homoglyphs (NFKC
  doesn't fold cross-script visual confusables — would require a
  Unicode confusables map; documented in `docs/3-security-audit.md`);
  variable-fed base64 concat (`b64decode(s + t)` — taint analysis,
  out of scope); name-indirected calls (`getattr(__builtins__,
  "eval")(...)` — beyond the AST's name-matching limit); JS/TS/shell
  AST analysis.  Per-skill opt-in via `audit.strict: true` in
  `.skilleval.yaml`; per-skill size cap via
  `audit.max_file_bytes: <n>`.  Also exposes `--max-file-bytes`
  independently for fine-grained control.

- **`skillctl eval audit --format=github`** — emits GitHub Actions
  workflow commands (`::error::` / `::warning::` / `::notice::`, one per
  finding) so audit findings appear as inline annotations on the
  offending lines of pull-request files.  CRITICAL → error, WARNING →
  warning, INFO → notice.  INFO findings are suppressed by default and
  surfaced via a single aggregate `::notice::` summarising what was
  hidden (so the GitHub 10-per-level cap isn't burned by low-severity
  noise — pass `--verbose` to render each one).  Each skill's findings
  are wrapped in a `::group::` collapse with a one-line
  `PASSED|FAILED — score/100 — N critical, N warning, N info` summary
  inside, so the collapsed CI log still shows a quick pass/fail signal.
  `--quiet` additionally routes a one-line per-skill summary to stderr
  (workflow commands stay on stdout).  Pair with `--fail-on-warning` to
  block PR merges on findings.  The workflow template at
  `examples/workflows/skill-audit.yml`, the README's CI snippet, and
  `docs/3-security-audit.md` are updated to use and document this
  format.

- **README rewrite — sharpened around the governance-layer framing.**
  The headline is "the governance layer for agent skills" and the lead
  is the lifecycle CLI itself: validate, audit, apply, bump, diff,
  install, describe, logs, serve.  Self-hosting is called out as the
  point of differentiation vs. vendor-hosted skills (skills often encode
  internal IP and security-sensitive review rules; teams want them on
  infra they control).  The capability matrix labels each feature with
  its actual maturity (`stable` / `beta` / `experimental`) — honest about
  the optimizer being a research preview, but not hidden.  A "What this
  replaces" table frames the alternative as the hand-rolled pipeline
  most teams build (bash script + gitleaks + bumpversion + custom
  harness), making the integration story explicit.
- **Copy-paste-ready CI workflow template** at
  `examples/workflows/skill-audit.yml`.  The README's CI snippet is real
  and runnable; this file is the maintained source.
- **CI `dogfood-audit` job** — runs `skillctl eval audit` against the
  example skills shipped in `examples/`.  Catches README-drift the
  moment an example stops being clean.

### Test count

The unit-test count quoted in this CHANGELOG hadn't been bumped since
v0.1.0b1 (292 tests).  Current state: **605 unit tests + 10 Bedrock
integration tests** — measured with `pytest --collect-only -q -m "not
integration"`.  The README and `AGENTS.md` are now updated to match.

- `LICENSE` file (MPL-2.0).  The license was advertised in `pyproject.toml`,
  README, the landing page, and the plugin manifest but the actual text
  was never committed; PyPI and GitHub now both render the correct
  license.
- `CONTRIBUTING.md` with dev setup, project conventions, and PR guidelines.
- README now links `LICENSE`, `SECURITY.md`, and `CONTRIBUTING.md`.
- CI `build-smoke` job: builds the sdist + wheel from a clean checkout,
  installs the wheel into a fresh venv, and verifies that
  `skillctl version` matches `skillctl/version.py`.  This is the test
  that would have caught the half-finished `skillctl → skillsops` rename
  artifacts.

### Changed

- `pyproject.toml` now uses `dynamic = ["version"]` reading from
  `skillctl.version.__version__` — single source of truth.
- `apply` no longer requires a namespace when the skill goes only to the
  local store.  The local store is single-user; only the **remote**
  registry needs namespaces.  Bare-name skills work end-to-end with
  `apply --local` / `install <path>`, fixing the README's "Already have
  skills?" path.
- `validate`, `eval audit`, `apply`, and `create skill` now print a
  one-line "Next:" breadcrumb on success.  All four suppress when stdout
  is not a TTY (CI / piped output).  `validate` additionally suppresses
  for `--json`; `eval audit` for `--quiet` and `--format {json,html}`.
  The transitive `cmd_apply` invocations inside `install <path>` /
  `install --from-url` also suppress to avoid telling the user to run
  the command they're already running.
- PyPI publish workflow switched to **Trusted Publishing (OIDC)** —
  removes the long-lived `PYPI_API_TOKEN` secret from the repo.  An
  environment-protected job + a tag/version equality check guard the
  release.
- **Internal: `apply_skill` library function.**  Extracted the apply
  lifecycle (validate → push → optional remote publish, with the
  security gate) from `cmd_apply` into a real `apply_skill(path, *,
  dry_run, local, registry_url, token) -> ApplyResult` function in
  `skillctl/_cli_helpers.py`.  `cmd_apply` is now a thin CLI shim that
  formats the result; `cmd_install` calls `apply_skill` directly,
  removing the synthetic-`argparse.Namespace` injection that the
  PR #1 review flagged as an anti-pattern.  The `_skip_breadcrumb`
  arg is no longer needed — `cmd_install` doesn't transit through
  `cmd_apply` anymore, so there's nothing to suppress.  No
  user-visible behaviour change: same exit codes, same stdout/stderr
  output (including the pre-existing per-error inline format on
  validation failures).
- **Internal: `cmd_logout` uses the typed config.**  Removed the last
  raw-YAML round-trip (`yaml.safe_load(config_path.read_text())` →
  `yaml.dump(...)` → `write_text`).  Now goes through `load_config()`
  / `save_config()`, which means logout writes the credentials file
  atomically with mode 0o600 via `atomic_write_secret` — strictly
  safer.  Also preserves unrelated config fields (registry URL,
  optimizer budget) instead of round-tripping the whole dict.
- **Internal: registry HTTP boilerplate consolidated.**  The five
  duplicated try/except `urllib.request.urlopen` blocks across
  `cmd_get_skills_remote`, `cmd_get_skill --remote`, `cmd_logs`,
  `cmd_token_create`, and `_publish_to_registry` now go through a single
  `_registry_request(method, url, *, token, body, content_type, timeout)`
  helper in the new `skillctl/_cli_helpers` module.  The helper raises
  `SkillctlError(code="E_REGISTRY_HTTP" | "E_REGISTRY_UNREACHABLE", ...)`,
  which the dispatch layer's `except SkillctlError` block already
  formats and prints — so behaviour is preserved (same exit codes), with
  slightly more structured / consistent error text across all five
  paths.  Bonus: the previously-broken FastAPI envelope inspection in
  `cmd_token_create` (`err.get('what', err.get('detail', body_text))`,
  which printed a Python `repr` against the actual `{"detail": {"what":
  ...}}` shape) now correctly extracts the `what` field.  Two
  `raise Exception(...)` in `_publish_to_registry` flagged in the
  `v0.1.0b4` security review are also now `SkillctlError`.  The other
  CLI helpers (`_get_registry_url`, `_load_config`, etc.) moved to the
  same new module; they remain importable from `skillctl.cli` for test
  monkeypatching.

### Removed

- Stale `skillctl.svg` (orphaned from the rename).  `skillsops.svg` is
  the canonical logo.
- `dist/`, `build/`, `*.egg-info/`, `.coverage`, `.understand-anything/`
  cleaned out of the working tree (all already gitignored; physical
  files removed locally).  `.understand-anything/` added to `.gitignore`.

## v0.1.0b4 (2026-05-12) — Security hardening

This release addresses the critical and high-severity findings from the
v0.1.0b3 security review.  See SECURITY.md for the threat model and
production hardening checklist.

### Security

- **SSRF protection on `install --from-url`**: every host is resolved and
  rejected if any A/AAAA address is loopback, private (RFC1918),
  link-local (incl. `169.254.169.254`), reserved, or a known cloud
  metadata IPv6.  Redirects are followed manually with a hard cap and
  re-validation each hop.  Responses are size-capped (5 MiB by default).
  `file://`/`ftp://` continue to be rejected.
- **Audit log is now hash-chained**: each entry's HMAC includes the
  previous entry's signature, so deletion or reordering is detectable.
  `verify_integrity()` returns the count of broken-chain entries.  The
  log file is created with mode `0o600`.
- **HMAC key decoupling**: the registry server no longer auto-generates
  an HMAC key in `data_dir` by default.  It reads from `--hmac-key`,
  `SKILLCTL_HMAC_KEY`, or refuses to start unless `--auto-generate-hmac-key`
  is passed.  This makes operators wire up real key management.
- **`--auth-disabled` is now localhost-only**: the server refuses to
  start when `--auth-disabled` is combined with a non-loopback bind.
  The default `--host` for `skillctl serve` is now `127.0.0.1` (was
  `0.0.0.0`).
- **Rate limiting**: `slowapi` is added to the `server` extra and
  installed by `create_app` (default 60 req/min/IP).
- **CORS + TrustedHost middleware**: installed by default, scoped to the
  configured host.  No browser cross-origin access unless `--cors-origin`
  is passed explicitly.
- **`/api/v1/audit` endpoint** (admin-only): returns recent events plus
  the integrity check report.  `skillctl logs` now reads from this
  endpoint.
- **Atomic credential file writes**: `~/.skillctl/config.yaml`, the
  HMAC key file, and any future secret are written via
  `os.open(..., O_CREAT|O_EXCL|O_WRONLY, 0o600)` — no TOCTOU window in
  which they are world-readable.
- **Read permission is now namespace-scoped**: previously, any token
  with any permission could read every namespace.  `read:<ns>`,
  `write:<ns>`, and unscoped `read` are honoured separately.
- **Permission strings are validated at `create_token`**: only `admin`,
  `read`, `read:<ns>`, and `write:<ns>` (with `[a-z0-9-]+` namespaces)
  are accepted.
- **`name`/`version` re-validation in `GitHubBackend`**: every storage
  call re-validates against a strict regex before any FS or git op.
- **`GIT_ASKPASS` instead of PAT-in-URL**: the GitHub PAT is no longer
  embedded in the clone URL or argv; it's supplied to git via a
  one-shot helper script that reads the token from an env var.
- **`getpass.getpass` for token entry**: the `configure` wizard no
  longer echoes auth tokens to the terminal or shell history.

### Changed

- `RegistryConfig.host` default changed from `0.0.0.0` to `127.0.0.1`.
  Operators that bind publicly must now pass `--host 0.0.0.0` explicitly.
- `RegistryConfig` gained `auto_generate_hmac_key`, `allowed_hosts`,
  and `cors_allow_origins` fields.
- `AuditLogger.AuditEvent` gained a `prev_signature` field.
- `slowapi>=0.1.9` is now listed in the `server` extra.
- The custom YAML parser in `eval/audit/structure_check.py` was replaced
  with `yaml.safe_load` (PyYAML is already a hard dep).  Skills with
  YAML constructs the custom parser silently dropped (anchors, tag
  types) are now correctly handled.
- `InstallationTracker` is now a context manager and releases its
  `flock` if `__init__` fails.

### Added

- `skillctl/_secure.py` — shared `safe_urlopen` and `atomic_write_secret`
  helpers.
- `tests/test_security.py` — 51 regression tests covering each fix.
- `SECURITY.md` — threat model, controls, hardening checklist,
  vulnerability reporting process.

## v0.1.0b3 (2026-05-04)

### Added

- **`eval validate` subcommand**: validates `evals/evals.json` and `evals/eval_queries.json` schemas without running LLM calls — catches structural errors before expensive evaluations
- **Eval scaffolding in `create skill`**: `skillctl create skill` now generates `evals/evals.json` and `evals/eval_queries.json` templates alongside `skill.yaml` and `SKILL.md`
- **Target alias `claude-code`**: `--target claude-code` is accepted as an alias for `--target claude` in install/uninstall commands
- **Realistic example skills**: replaced placeholder examples with three real-world skills (tdd-workflow, api-design-reviewer, dependency-scanner) with full eval files

### Fixed

- **`validate` exit code**: warnings-only validation now exits 0 (was exit 2), matching standard CLI conventions; `--strict` still exits 1 for warnings
- **`validate` output**: prints `"✓ Valid (with N warnings)"` instead of silently printing nothing when there are warnings but no errors
- **Audit INFO findings**: `eval audit` now shows finding codes inline (e.g., `SEC-002`) even without `--verbose`, so users know what to look up
- **Install help text**: `skillctl install --help` now documents the auto-apply behavior for local paths
- **Target error message**: unknown target errors now list accepted aliases alongside primary target names

## v0.1.0b2 (2026-05-01)

### Added

- **Claude Code plugin** (`plugin/`): 3 skills + 14 MCP tools exposing skillctl as a library
- **Multi-IDE install**: `skillctl install/uninstall` distributes governed skills to Claude Code, Cursor, Windsurf, Copilot, and Kiro with native frontmatter translation
- **`--dry-run` for install**: preview what would be installed without writing files
- **Category taxonomy**: optional `metadata.category` field with 12 known categories and validation
- **Export command**: `skillctl export` creates portable tar.gz/zip archives from the local store
- **Store consistency check**: `verify_consistency()` detects dangling refs and orphaned blobs
- **Expanded `doctor` checks**: directory permissions, optional dep importability, install target detection, store consistency
- **CI pipeline**: GitHub Actions with lint (ruff), format, typecheck (pyright), tests (3.10/3.12/3.13), and security (pip-audit) — all blocking
- **Plugin hint**: `skillctl` emits `<claude-code-hint>` on stderr when running inside Claude Code
- **`bump` command**: `skillctl bump` auto-increments the version in skill.yaml (supports `--major`, `--minor`, `--patch`)
- **`logs` command**: `skillctl logs <name>` now fetches audit events from the registry API instead of showing a stub message
- **`eval --help`**: running `skillctl eval` with no subcommand now correctly prints the eval parser help
- **Quickstart hint**: main CLI `--help` now shows a quickstart example in the epilog
- **`eval init` generates `.skilleval.yaml`**: `skillctl eval init` now also creates a `.skilleval.yaml` config file alongside `evals.json` and `eval_queries.json`
- **Import command**: `skillctl import archive.tar.gz` restores skills from exported archives (reverse of `export`)
- **Install from local path**: `skillctl install ./my-skill --target cursor` auto-applies then installs (no store ref needed)
- **Install from URL**: `skillctl install --from-url https://... --target all` downloads and installs a remote SKILL.md
- **SKILL.md first-class ingest**: bare SKILL.md files with frontmatter are fully valid — no skill.yaml needed for local operations
- **`skillctl:` governance block**: SKILL.md frontmatter supports `skillctl.namespace`, `skillctl.version`, `skillctl.category`, `skillctl.tags`, `skillctl.capabilities`, `skillctl.authors`
- **Bare skill names**: names without namespace (e.g., `code-reviewer`) are valid locally; `apply` requires namespace for store/registry
- **STR-021 token budget warning**: audit warns when SKILL.md body exceeds ~4,000 tokens
- **Network error messages**: all registry HTTP errors now suggest `skillctl doctor` for diagnosis

### Fixed

- **Security**: `import_skills()` now validates tar and zip archive member paths before extraction, rejecting absolute paths and `..` traversal
- **Security**: `download_skill()` now rejects non-HTTP(S) URL schemes (e.g., `file://`, `ftp://`) to prevent SSRF
- **Security**: `download_skill()` sanitizes frontmatter-derived skill names used as directory paths
- **Compat**: `import_skills()` no longer uses `tarfile.extractall(filter="data")` which requires Python 3.12+; works on 3.10+
- Removed dead code (unused set expression) in `structure_check.py`
- GitHub token sanitized in all git subprocess output (stdout, stderr, cmd args)
- Auth error messages now distinguish missing vs invalid/expired tokens
- Audit log `verify_integrity()` tracks parse errors instead of silently ignoring corrupt lines
- File locking (`fcntl.flock`) on `installations.json` prevents concurrent install commands from corrupting state
- Atomic write failures in store and installation tracker now raise `SkillctlError` with actionable messages (`E_STORE_WRITE`, `E_STATE_WRITE`)
- Empty skill content is rejected before installation (`E_EMPTY_CONTENT`)
- Swallowed exceptions in config, install, utils, and CLI validation now warn on stderr
- FTS search pagination clamped to safe bounds (limit 1-500, offset 0-100000)
- Sensitive config keys (`token`, `secret`) warn about shell history exposure
- 67 pyright type errors resolved across 11 files; typecheck is now blocking in CI
- All ruff lint and format issues resolved across the codebase

### Changed

- **Branding**: all user-facing prose now says "SkillsOps" (CLI command `skillctl` unchanged)
- `litellm` minimum bumped to 1.83.14 (fixes 11 CVEs in aiohttp and python-dotenv)
- Coverage exclusions configured for integration-only files; badge reflects unit-testable code (81%)
- `MANIFEST.in` and `include-package-data` added for correct PyPI distribution
- Broken `docs/REFERENCE.md` links updated to `docs/1-skill-format.md` (website footer and eval scaffold)
- Fixed `[all]` optional-dependency group referencing wrong package name (`skillctl` → `skillsops`)
- Landing page deployed at `site/index.html` with GitHub Pages
- Documentation reorganized into numbered lifecycle sequence (`docs/0-architecture.md` through `docs/5-installation.md`)

## v0.1.0b1 (2026-04-23)

First public beta.

### Security

- Credential files (config.yaml, hmac.key) written with 0600 permissions
- Path traversal protection in registry storage (content hash validation)
- FTS5 query injection fix in search (embedded double quotes)
- Upload size limit (50 MB) on publish endpoint
- Security audit gates remote publish — CRITICAL findings block `skillctl apply`
- Thread-safe security scan configuration (no more mutable global state)

### Architecture

- LLM provider consolidated to Amazon Bedrock only (via `anthropic.AnthropicBedrock`)
- Default model: `us.anthropic.claude-opus-4-6-v1` (Claude Opus 4.6)
- `--provider` flag removed from optimizer CLI
- `EvalError` now subclasses `SkillctlError` (was a full duplicate)
- `SkillManifest.to_dict()` eliminates serialization duplication
- Shared utilities in `skillctl/utils.py` (parse_ref, read_skill_name)
- Eval CLI integrated via direct function calls (was sys.argv mutation hack)
- `python-multipart` moved from core deps to server optional group

### CLI

- `skillctl apply` now runs security scan before remote publish
- `skillctl create skill` refuses to overwrite existing files
- `skillctl validate --strict` correctly includes all warning types
- `cmd_doctor` treats missing store as warning, not error (fresh install friendly)
- `_require_registry_url` raises SkillctlError instead of sys.exit
- parse_ref rejects empty name ("@1.0.0") and empty version ("ns/name@")

### Eval

- `trigger_precision`/`no_trigger_precision` renamed to `trigger_recall`/`no_trigger_recall`
- `EvalResult.audit_findings` carries structured findings for optimizer analysis
- Optimizer failure analyzer uses full audit findings for better LLM diagnosis

### Dead code removed

- `require_permission` (auth.py), `validate_semver` wrapper (validator.py)
- `s3_bucket`/`s3_prefix` config fields, 5 dead exports from `_claude.py`
- Duplicate `_read_skill_name` (5 copies), `_parse_ref` (2 copies)

### Tests

- 292 tests (282 unit + 10 integration against real Bedrock)
- New: test_manifest.py, test_validator.py, test_content_store.py, test_utils.py, test_cli.py, test_integration_bedrock.py

### CLI — kubectl-style verb alignment

- `skillctl apply [path]` — validate + push to local store; publish to remote if configured (replaces `push` and `publish`)
- `skillctl create skill <name>` — scaffold a new skill (replaces `init`)
- `skillctl get skills` — list skills from local store or remote with `--remote` (replaces `list` and `search`)
- `skillctl get skill <ref>` — pull/show a specific skill (replaces `pull`)
- `skillctl describe skill <ref>` — rich detail view (new)
- `skillctl delete skill <ref>` — remove a skill version from local store (new)
- `skillctl logs <name>` — audit trail stub (new, requires registry)
- All old commands (`init`, `push`, `pull`, `list`, `publish`, `search`) kept as backward-compatible aliases

## v0.1.0 (2026-03-24)

Initial release — CLI governance platform for agent skills.

### CLI

- `skillctl init` — scaffold new skills (skill.yaml + SKILL.md)
- `skillctl validate` — schema validation, semver, capability checks (`--strict`, `--json`)
- `skillctl push` / `pull` / `list` — local content-addressed store
- `skillctl diff` — version comparison with breaking change detection
- `skillctl doctor` — environment diagnostics
- `skillctl login` / `logout` — GitHub device flow authentication
- `skillctl config set/get` — configuration management

### Registry Server

- `skillctl serve` — headless FastAPI server with REST API
- `skillctl publish` / `search` — remote registry interaction
- `skillctl token create` — scoped API tokens (read, write, admin)
- Token-based auth with namespace-scoped permissions
- SQLite metadata index with FTS5 full-text search
- Content-addressed blob storage (filesystem backend)
- GitHub repository as storage backend
- HMAC-SHA256 signed audit log
- Docker deployment (Dockerfile + docker-compose.yml)

### Eval Suite

- `skillctl eval audit` — security scan with A–F grading (100-point scale)
- `skillctl eval functional` — with/without skill baseline comparison
- `skillctl eval trigger` — activation reliability testing
- `skillctl eval report` — unified scoring (40% audit, 40% functional, 20% trigger)
- `skillctl eval snapshot` / `regression` — baseline and regression detection
- `skillctl eval compare` — side-by-side skill comparison
- `skillctl eval lifecycle` — version tracking and change detection

### Skill Optimizer

- `skillctl optimize` — automated improvement loop (eval → failure analysis → LLM variants → promotion)
- `skillctl optimize history` / `diff` — run provenance and diffs
- Budget enforcement, plateau detection, dry-run mode
- Amazon Bedrock LLM provider via AnthropicBedrock SDK

### Skill Format

- `skill.yaml` manifest with metadata, spec, governance sections
- Backward compatibility with plain SKILL.md files (auto-wrap)
- Multi-file archive support (.zip, .tar.gz)
