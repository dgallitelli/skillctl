# SKILL.md as First-Class Ingest Format

**Date:** 2026-04-28
**Goal:** Make bare SKILL.md files (no companion skill.yaml) fully valid for all skillctl operations, by parsing frontmatter — including a `skillctl:` nested block for governance metadata.

## Motivation

Every AI coding IDE uses markdown with YAML frontmatter as its native skill format. Today skillctl treats bare SKILL.md as a second-class citizen: it emits a warning, hardcodes version to `0.0.0`, leaves description empty (failing validation), and stores the raw file including `---` markers as inline content.

Users with existing skills in Claude Code, Cursor, or Kiro shouldn't need to create a skill.yaml to run `skillctl validate`, `skillctl eval audit`, or `skillctl install --target cursor`. The governance pipeline should work on the format they already use.

## Frontmatter format

Standard IDE fields at the top level. Governance fields in a `skillctl:` nested block:

```yaml
---
name: code-reviewer
description: Reviews code for security issues
allowed-tools: Read Grep
paths: "**/*.py"
skillctl:
  namespace: my-org
  version: 1.2.0
  category: security
  tags: [security, code-review]
  capabilities: [read_file, read_code]
---

When reviewing code, check for...
```

### Field responsibilities

| Field | Owner | Read by |
|-------|-------|---------|
| `name` | IDE standard | All IDEs + skillctl |
| `description` | IDE standard | All IDEs + skillctl |
| `allowed-tools` | Claude Code | Claude Code + skillctl (passthrough) |
| `paths` | Claude Code | Claude Code + skillctl (translates for other IDEs) |
| `disable-model-invocation` | Claude Code | Claude Code + skillctl (translates for other IDEs) |
| `skillctl.namespace` | skillctl | skillctl only (IDEs ignore) |
| `skillctl.version` | skillctl | skillctl only |
| `skillctl.category` | skillctl | skillctl only |
| `skillctl.tags` | skillctl | skillctl only |
| `skillctl.capabilities` | skillctl | skillctl only |

### Qualified name composition

When skillctl needs the full `namespace/name` form (for store, registry, diff):
- If `skillctl.namespace` exists: `{skillctl.namespace}/{name}` → `my-org/code-reviewer`
- If `skillctl.namespace` is absent: bare `name` is used → `code-reviewer`

Bare names are valid for local operations (validate, eval, install). `apply` (which pushes to the store/registry) requires a namespace — if missing, it errors with a fix suggestion to add `skillctl.namespace`.

## Precedence

When both `skill.yaml` and `SKILL.md` exist in a directory:

1. **skill.yaml wins** — the explicit governance manifest is the authority
2. SKILL.md is loaded only as content (via `spec.content.path`), frontmatter is not parsed for metadata

When only `SKILL.md` exists:

1. `skillctl:` block fields populate governance metadata
2. Standard frontmatter fields (`name`, `description`) populate manifest metadata
3. Defaults fill remaining gaps (`version` → `0.1.0`)

## Changes

### `skillctl/manifest.py` — `ManifestLoader._wrap_markdown()`

**Current behavior:** Ignores frontmatter, stores entire file as inline content, hardcodes name from directory and version as `0.0.0`.

**New behavior:**

```python
def _wrap_markdown(self, path: Path) -> tuple[SkillManifest, list[Warning]]:
    content = path.read_text()
    warnings = []
    frontmatter, body = _parse_frontmatter(content)

    if not frontmatter:
        # No frontmatter at all — legacy behavior with warning
        name = path.parent.name or path.stem
        manifest = SkillManifest(
            metadata=SkillMetadata(name=name, version="0.0.0"),
            spec=SkillSpec(content=ContentRef(inline=content)),
        )
        warnings.append(Warning(
            code="W_AUTO_WRAPPED",
            message=f"Auto-wrapped {path.name} in minimal manifest (no frontmatter found)",
            hint="Add YAML frontmatter with name and description",
        ))
        return manifest, warnings

    # Extract skillctl governance block
    governance = frontmatter.get("skillctl", {})

    # Compose qualified name
    base_name = frontmatter.get("name") or path.parent.name or path.stem
    namespace = governance.get("namespace")
    qualified_name = f"{namespace}/{base_name}" if namespace else base_name

    # Build manifest from frontmatter
    version = governance.get("version", "0.1.0")
    description = frontmatter.get("description", "")
    category = governance.get("category")
    tags = governance.get("tags", [])
    capabilities = governance.get("capabilities", [])

    manifest = SkillManifest(
        metadata=SkillMetadata(
            name=qualified_name,
            version=version,
            description=description,
            tags=tags,
            category=category,
        ),
        spec=SkillSpec(
            content=ContentRef(inline=body),
            capabilities=capabilities,
        ),
    )

    if not description:
        warnings.append(Warning(
            code="W_NO_DESCRIPTION",
            message="SKILL.md frontmatter has no description",
            hint="Add a description field for better governance and IDE discovery",
        ))

    return manifest, warnings
```

The return type changes from `tuple[SkillManifest, Warning]` to `tuple[SkillManifest, list[Warning]]`. The `load()` method must be updated to extend `warnings` with the returned list instead of appending a single warning.

A shared `_parse_frontmatter()` function replaces the duplicated parsing in `install.py` and is reused here.

### `skillctl/manifest.py` — new `_parse_frontmatter()`

```python
def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split markdown content into frontmatter dict and body.

    Returns ({}, original_content) if no frontmatter found.
    """
    if not content.startswith("---"):
        return {}, content
    try:
        end = content.index("---", 3)
        fm_text = content[3:end].strip()
        body = content[end + 3:].strip()
        fm = yaml.safe_load(fm_text) or {}
        return fm, body
    except (ValueError, yaml.YAMLError):
        return {}, content
```

### `skillctl/install.py` — reuse shared parser

Replace the local `_parse_skill_frontmatter()` with an import of `_parse_frontmatter` from `manifest.py`. No behavior change — just deduplication.

### `skillctl/validator.py` — relax namespace requirement

**Current:** `VAL-NAME-FORMAT` requires `namespace/skill-name` (must contain `/`).

**New:** Allow bare names. Add an info-level `VAL-NAME-NO-NAMESPACE`:
- If name contains `/`: valid (current behavior)
- If name has no `/`: valid, but emit info: "No namespace — add `skillctl.namespace` in frontmatter or use skill.yaml to publish to registry"

The `VAL-NAME-FORMAT` regex check (lowercase, hyphens) still applies to both forms.

### `skillctl/cli.py` — `cmd_apply()` namespace gate

When `apply` encounters a skill with no namespace (bare name), it blocks with:

```python
SkillctlError(
    code="E_NO_NAMESPACE",
    what=f"Skill '{name}' has no namespace",
    why="The store and registry require namespaced names to prevent collisions",
    fix="Add 'skillctl.namespace: my-org' to SKILL.md frontmatter, or create a skill.yaml",
)
```

This only fires on `apply`. All other commands (validate, eval, install, describe) work with bare names.

### Warning behavior summary

| Scenario | Warnings | Validation |
|----------|----------|------------|
| SKILL.md with `name` + `description` + `skillctl:` block | None | Passes |
| SKILL.md with `name` + `description`, no `skillctl:` block | None | Passes (version defaults to 0.1.0) |
| SKILL.md with `name`, no `description` | `W_NO_DESCRIPTION` | Passes (description optional for SKILL.md) |
| SKILL.md with no frontmatter | `W_AUTO_WRAPPED` | Passes with defaults |
| SKILL.md with `name` but no namespace, running `apply` | — | Fails with `E_NO_NAMESPACE` |

### Validator description requirement change

Currently `VAL-DESC-REQUIRED` fires when description is empty. For SKILL.md-loaded manifests, this becomes a warning (`W_NO_DESCRIPTION`) instead of an error. The rationale: a Claude Code skill without a description still works — it just won't be auto-invoked by the IDE. Governance shouldn't block usage; it should inform.

For skill.yaml-loaded manifests, the description remains required (error). The distinction: if you wrote a skill.yaml, you committed to the governance format and should fill in all required fields.

## Testing

- `tests/test_manifest.py`: add tests for SKILL.md with frontmatter (with/without `skillctl:` block, with/without description, no frontmatter)
- `tests/test_validator.py`: add tests for bare names (no namespace), namespace info message
- `tests/test_cli.py` or `tests/test_cli_smoke.py`: test that `apply` blocks on bare names
- `tests/test_install.py`: verify `_parse_frontmatter` import works (no behavior change)

## Documentation

- `docs/REFERENCE.md`: add "SKILL.md Format" section documenting frontmatter fields and the `skillctl:` block
- `README.md`: mention that bare SKILL.md files are valid input
- `AGENTS.md`: note that `_parse_frontmatter` is the shared parser
