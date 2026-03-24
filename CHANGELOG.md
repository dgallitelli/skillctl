# Changelog

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
- Bedrock and Anthropic LLM provider support

### Skill Format

- `skill.yaml` manifest with metadata, spec, governance sections
- Backward compatibility with plain SKILL.md files (auto-wrap)
- Multi-file archive support (.zip, .tar.gz)
