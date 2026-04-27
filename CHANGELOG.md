# Changelog

## Unreleased

### Fixed

- File locking (`fcntl.flock`) on `installations.json` prevents concurrent install commands from corrupting state
- Atomic write failures in store and installation tracker now raise `SkillctlError` with actionable messages (`E_STORE_WRITE`, `E_STATE_WRITE`)
- Empty skill content is rejected before installation (`E_EMPTY_CONTENT`)

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
