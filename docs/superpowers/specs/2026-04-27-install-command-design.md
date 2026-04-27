# Install Command: Distribute Skills to AI Coding IDEs

**Date:** 2026-04-27
**Goal:** Add `skillctl install` / `uninstall` commands that distribute governed skills to Claude Code, Cursor, Windsurf, GitHub Copilot, and Kiro — completing the lifecycle from create → govern → distribute.

## Motivation

Today `skillctl apply` validates and stores a skill, but doesn't make it usable in any IDE. Users must manually copy files to each tool's skill directory. With 5+ AI coding tools in common use, maintaining copies is tedious and error-prone.

`install` bridges governance and usage: after a skill passes quality gates, one command puts it where every IDE can find it.

## CLI Interface

### install

```bash
skillctl install <ref-or-path> --target <ide,...|all> [--global] [--force]
```

- `<ref-or-path>`: a store reference (`my-org/my-skill@1.0`) or a source path (`./my-skill`). If a path is given, runs `apply` first (validate + audit + store), then installs. If apply fails (validation errors or critical audit findings), install aborts with the apply error — no partial install.
- `--target`: required. Comma-separated list of IDE names or `all`. No default — forces the user to be explicit.
- `--global`: install to user-level directory instead of project-level. Not all targets have a global path (Cursor and Copilot are project-only).
- `--force`: overwrite files modified since last install without prompting.

Valid target names: `claude`, `cursor`, `windsurf`, `copilot`, `kiro`.

`all` auto-detects which IDEs are present by checking for their config directories in the current project (`.claude/`, `.cursor/`, `.windsurf/`, `.github/`, `.kiro/`). If `--global`, checks user-level directories instead.

### uninstall

```bash
skillctl uninstall <ref> --target <ide,...|all>
```

Removes the skill from the specified targets. Only removes files that skillctl created (tracked in installations.json). Warns if a file was modified since installation.

### get installations

```bash
skillctl get installations [--target <ide>] [--json]
```

Lists all skillctl-managed installations. Optionally filtered by target IDE. Default output is a human-readable table; `--json` for structured output.

## Architecture

### New module: `skillctl/install.py`

Single module with four public functions:

```python
install_skill(ref: str, targets: list[str], global_scope: bool, force: bool) -> list[InstallResult]
uninstall_skill(ref: str, targets: list[str]) -> list[UninstallResult]
list_installations(target: str | None) -> list[Installation]
detect_targets(global_scope: bool) -> list[str]  # for --target all
```

### Target registry

A dict mapping IDE names to their path conventions and format functions:

```python
@dataclass
class TargetConfig:
    name: str
    project_path: Callable[[str], Path]  # skill_name -> file path
    global_path: Callable[[str], Path] | None
    format_fn: Callable[[str, dict, str], str]  # name, frontmatter, body -> file content
    detect_dir: str  # directory to check for auto-detection
```

### Targets

| Target | Project path | Global path | Detect dir |
|--------|-------------|-------------|------------|
| `claude` | `.claude/skills/{name}/SKILL.md` | `~/.claude/skills/{name}/SKILL.md` | `.claude/` |
| `cursor` | `.cursor/rules/{name}.mdc` | None | `.cursor/` |
| `windsurf` | `.windsurf/rules/{name}.md` | `~/.codeium/windsurf/memories/global_rules.md` | `.windsurf/` |
| `copilot` | `.github/instructions/{name}.instructions.md` | None | `.github/` |
| `kiro` | `.kiro/steering/{name}.md` | `~/.kiro/steering/{name}.md` | `.kiro/` |

Claude is the only target that creates a directory per skill (with SKILL.md inside). All others create a single flat file.

Windsurf global is a special case: a single shared file. Install appends a section with markers:

```markdown
<!-- skillctl:my-org/my-skill:start -->
... skill content ...
<!-- skillctl:my-org/my-skill:end -->
```

Uninstall removes the marked section. Other content in the file is never touched.

### Frontmatter translation

Each target has a `format_fn` that takes the original frontmatter dict and body, and produces the target-native file content.

**Field mapping:**

| Source field | Claude | Cursor | Windsurf | Copilot | Kiro |
|-------------|--------|--------|----------|---------|------|
| `description` | `description` | `description` | `description` | — | `name` + `description` |
| `paths` (globs) | `paths` | `globs` | `trigger: glob` | `applyTo` | `inclusion: fileMatch` + `fileMatchPattern` |
| `disable-model-invocation: true` | passthrough | `alwaysApply: false` | `trigger: manual` | — | `inclusion: manual` |
| `disable-model-invocation: false` | passthrough | `alwaysApply: false` | `trigger: model_decision` | — | `inclusion: auto` |
| no paths, no disable | passthrough | `alwaysApply: true` | `trigger: always_on` | — | `inclusion: always` |
| `allowed-tools` | passthrough | dropped + warn | dropped + warn | dropped + warn | dropped + warn |
| `context` | passthrough | dropped + warn | dropped + warn | dropped + warn | dropped + warn |
| `model` | passthrough | dropped + warn | dropped + warn | dropped + warn | dropped + warn |

Body markdown is copied verbatim. Only frontmatter is translated.

When a field is dropped, a warning is printed to stderr: `Warning: 'allowed-tools' not supported by cursor, skipping`.

### Installation tracking

State file: `~/.skillctl/installations.json`

```json
{
  "my-org/my-skill@1.0.0": {
    "claude": {
      "path": "/absolute/path/.claude/skills/my-skill/SKILL.md",
      "scope": "project",
      "installed_at": "2026-04-27T15:00:00Z",
      "content_hash": "abc123..."
    }
  }
}
```

`content_hash` is the SHA-256 of the file as written. Used to detect if the user modified the file after installation. If modified, `uninstall` warns and `install` requires `--force` to overwrite.

The file uses the same atomic write pattern as the rest of skillctl (tempfile + os.replace).

### Safety rules

1. skillctl only removes files it created (tracked in installations.json)
2. Before overwriting, check if file was modified since install (hash mismatch → warn + require `--force`)
3. `--target all` only installs to IDEs that are detected (config dir exists)
4. Target directory is created if it doesn't exist (e.g., `.cursor/rules/` on first install)
5. Never touch files not tracked in installations.json

### Error handling

All errors use `SkillctlError(code, what, why, fix)`:

| Error | Code | Fix |
|-------|------|-----|
| Ref not in store | `E_NOT_FOUND` | "Run 'skillctl apply' first or check 'skillctl get skills'" |
| Target IDE not detected | `E_TARGET_NOT_FOUND` | "No {target} config directory found. Create it or use --force" |
| File modified since install | `E_FILE_MODIFIED` | "File was modified externally. Use --force to overwrite" |
| No global path for target | `E_NO_GLOBAL` | "{target} does not support global installation" |

### CLI registration

Three new entries in `cli.py` dispatch:

- `install` → `cmd_install(args)`
- `uninstall` → `cmd_uninstall(args)`
- `get installations` → `cmd_get_installations(args)` (added to existing `get` subparser)

### MCP server

Add one tool to the plugin MCP server:

- `skillctl_install(ref, targets, global_scope=False)` — so Claude Code can install skills to other IDEs during workflow

### Testing

- `test_install.py`: unit tests for frontmatter translation (all 5 targets), install/uninstall file operations (using tmp_path), installation tracking JSON round-trips, auto-detection logic, modified-file detection
- `test_cli_smoke.py`: add smoke tests for `install --help`, `uninstall --help`, `get installations`
- No integration tests needed — all file operations are local

### Documentation updates

- README.md: add install/uninstall to quickstart and feature table
- ARCHITECTURE.md: add install module to module map
- AGENTS.md: add install.py to project structure
- docs/REFERENCE.md: full CLI reference for install/uninstall/get installations
