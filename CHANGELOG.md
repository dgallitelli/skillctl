# Changelog

## Unreleased

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

- `litellm` minimum bumped to 1.83.14 (fixes 11 CVEs in aiohttp and python-dotenv)
- Coverage exclusions configured for integration-only files; badge reflects unit-testable code (81%)
- `MANIFEST.in` and `include-package-data` added for correct PyPI distribution

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
