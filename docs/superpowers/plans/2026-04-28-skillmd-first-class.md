# SKILL.md First-Class Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make bare SKILL.md files with frontmatter fully valid for all skillctl operations, parsing standard IDE fields plus a `skillctl:` nested block for governance metadata.

**Architecture:** Modify `ManifestLoader._wrap_markdown()` to parse frontmatter and build a real manifest. Relax `SchemaValidator._validate_name()` to allow bare names (no namespace). Gate namespace requirement in `cmd_apply()`. Deduplicate frontmatter parsing between manifest.py and install.py.

**Tech Stack:** Python 3.10+, pyyaml, existing skillctl modules

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `skillctl/manifest.py` | Modify | Add `_parse_frontmatter()`, rewrite `_wrap_markdown()`, update `load()` return handling |
| `skillctl/validator.py` | Modify | Relax `_validate_name()` to allow bare names, add `VAL-NAME-NO-NAMESPACE` info |
| `skillctl/install.py` | Modify | Replace `_parse_skill_frontmatter()` with import from manifest.py |
| `skillctl/cli.py` | Modify | Add namespace gate in `cmd_apply()` |
| `tests/test_manifest.py` | Modify | Add tests for SKILL.md frontmatter parsing |
| `tests/test_validator.py` | Modify | Add tests for bare names |
| `tests/test_cli_smoke.py` | Modify | Add test for apply namespace gate |
| `docs/REFERENCE.md` | Modify | Add SKILL.md format section |

---

### Task 1: Shared frontmatter parser and rewritten _wrap_markdown

**Files:**
- Modify: `skillctl/manifest.py`
- Test: `tests/test_manifest.py`

- [ ] **Step 1: Write failing tests for frontmatter parsing**

Append to `tests/test_manifest.py`:

```python
from skillctl.manifest import _parse_frontmatter


class TestParseFrontmatter:
    def test_with_frontmatter(self):
        content = "---\nname: my-skill\ndescription: Does stuff\n---\n\n# Body"
        fm, body = _parse_frontmatter(content)
        assert fm["name"] == "my-skill"
        assert fm["description"] == "Does stuff"
        assert body == "# Body"

    def test_no_frontmatter(self):
        content = "# Just markdown\n\nNo frontmatter here."
        fm, body = _parse_frontmatter(content)
        assert fm == {}
        assert body == content

    def test_invalid_yaml(self):
        content = "---\n: broken: yaml:\n---\n\nbody"
        fm, body = _parse_frontmatter(content)
        assert fm == {}
        assert body == content

    def test_skillctl_block(self):
        content = "---\nname: my-skill\nskillctl:\n  namespace: my-org\n  version: 2.0.0\n  category: security\n  tags: [a, b]\n  capabilities: [read_file]\n---\n\n# Body"
        fm, body = _parse_frontmatter(content)
        assert fm["skillctl"]["namespace"] == "my-org"
        assert fm["skillctl"]["version"] == "2.0.0"
        assert fm["skillctl"]["category"] == "security"
        assert body == "# Body"


class TestWrapMarkdownWithFrontmatter:
    def test_full_frontmatter_with_skillctl_block(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text("---\nname: code-reviewer\ndescription: Reviews code\nskillctl:\n  namespace: my-org\n  version: 1.2.0\n  category: security\n  tags: [sec]\n  capabilities: [read_file]\n---\n\n# Instructions")
        loader = ManifestLoader()
        manifest, warnings = loader.load(str(md))
        assert manifest.metadata.name == "my-org/code-reviewer"
        assert manifest.metadata.version == "1.2.0"
        assert manifest.metadata.description == "Reviews code"
        assert manifest.metadata.category == "security"
        assert manifest.metadata.tags == ["sec"]
        assert manifest.spec.capabilities == ["read_file"]
        assert "# Instructions" in manifest.spec.content.inline
        assert "---" not in manifest.spec.content.inline
        assert len(warnings) == 0

    def test_frontmatter_without_skillctl_block(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text("---\nname: simple-skill\ndescription: A simple skill\n---\n\nDo the thing.")
        loader = ManifestLoader()
        manifest, warnings = loader.load(str(md))
        assert manifest.metadata.name == "simple-skill"
        assert manifest.metadata.version == "0.1.0"
        assert manifest.metadata.description == "A simple skill"
        assert len(warnings) == 0

    def test_frontmatter_no_description_warns(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text("---\nname: no-desc\n---\n\nBody here.")
        loader = ManifestLoader()
        manifest, warnings = loader.load(str(md))
        assert manifest.metadata.name == "no-desc"
        assert any(w.code == "W_NO_DESCRIPTION" for w in warnings)

    def test_no_frontmatter_legacy_behavior(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text("# Just instructions\n\nNo frontmatter.")
        loader = ManifestLoader()
        manifest, warnings = loader.load(str(md))
        assert manifest.metadata.version == "0.0.0"
        assert any(w.code == "W_AUTO_WRAPPED" for w in warnings)

    def test_name_from_directory_when_not_in_frontmatter(self, tmp_path):
        skill_dir = tmp_path / "my-cool-skill"
        skill_dir.mkdir()
        md = skill_dir / "SKILL.md"
        md.write_text("---\ndescription: Has desc but no name\n---\n\nBody.")
        loader = ManifestLoader()
        manifest, warnings = loader.load(str(skill_dir))
        assert manifest.metadata.name == "my-cool-skill"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_manifest.py::TestParseFrontmatter -v`
Expected: ImportError — `_parse_frontmatter` does not exist

- [ ] **Step 3: Implement `_parse_frontmatter()` and rewrite `_wrap_markdown()`**

In `skillctl/manifest.py`, add the shared parser function (before the `ManifestLoader` class):

```python
def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split markdown content into frontmatter dict and body.

    Returns ({}, original_content) if no valid frontmatter found.
    """
    if not content.startswith("---"):
        return {}, content
    try:
        end = content.index("---", 3)
        fm_text = content[3:end].strip()
        body = content[end + 3 :].strip()
        fm = yaml.safe_load(fm_text) or {}
        return fm, body
    except (ValueError, yaml.YAMLError):
        return {}, content
```

Replace `_wrap_markdown()` in the `ManifestLoader` class:

```python
def _wrap_markdown(self, path: Path) -> tuple[SkillManifest, list[Warning]]:
    """Build a manifest from a SKILL.md file, parsing frontmatter if present."""
    content = path.read_text()
    warnings: list[Warning] = []
    frontmatter, body = _parse_frontmatter(content)

    if not frontmatter:
        name = path.parent.name or path.stem
        manifest = SkillManifest(
            metadata=SkillMetadata(name=name, version="0.0.0"),
            spec=SkillSpec(content=ContentRef(inline=content)),
        )
        warnings.append(
            Warning(
                code="W_AUTO_WRAPPED",
                message=f"Auto-wrapped {path.name} in minimal manifest (no frontmatter found)",
                hint="Add YAML frontmatter with name and description",
            )
        )
        return manifest, warnings

    governance = frontmatter.get("skillctl", {})

    base_name = frontmatter.get("name") or path.parent.name or path.stem
    namespace = governance.get("namespace")
    qualified_name = f"{namespace}/{base_name}" if namespace else base_name

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
        warnings.append(
            Warning(
                code="W_NO_DESCRIPTION",
                message="SKILL.md frontmatter has no description",
                hint="Add a description field for better governance and IDE discovery",
            )
        )

    return manifest, warnings
```

Update `load()` to handle the new `list[Warning]` return from `_wrap_markdown()`. Change lines 150-151 and 163-164:

```python
# In load(), replace:
#     manifest, warn = self._wrap_markdown(md_path)
#     warnings.append(warn)
# With:
    manifest, md_warnings = self._wrap_markdown(md_path)
    warnings.extend(md_warnings)
```

Do this in both places (directory path and direct .md path).

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_manifest.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add skillctl/manifest.py tests/test_manifest.py
git commit -m "feat: parse SKILL.md frontmatter with skillctl: governance block"
```

---

### Task 2: Relax validator namespace requirement

**Files:**
- Modify: `skillctl/validator.py`
- Test: `tests/test_validator.py`

- [ ] **Step 1: Write failing tests for bare names**

Append to `tests/test_validator.py`:

```python
class TestBareNameValidation:
    def test_bare_name_passes(self):
        manifest = _make_manifest(name="code-reviewer")
        result = SchemaValidator().validate(manifest)
        assert result.valid is True

    def test_bare_name_no_namespace_info(self):
        manifest = _make_manifest(name="code-reviewer")
        result = SchemaValidator().validate(manifest)
        infos = [w for w in result.warnings if w.code == "VAL-NAME-NO-NAMESPACE"]
        assert len(infos) == 1
        assert "namespace" in infos[0].message.lower()

    def test_namespaced_name_no_info(self):
        manifest = _make_manifest(name="my-org/code-reviewer")
        result = SchemaValidator().validate(manifest)
        infos = [w for w in result.warnings if w.code == "VAL-NAME-NO-NAMESPACE"]
        assert len(infos) == 0

    def test_bare_name_still_validates_format(self):
        manifest = _make_manifest(name="INVALID_NAME")
        result = SchemaValidator().validate(manifest)
        assert result.valid is False
        codes = [e.code for e in result.errors]
        assert "VAL-NAME-FORMAT" in codes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_validator.py::TestBareNameValidation -v`
Expected: FAIL — bare name `code-reviewer` currently fails `VAL-NAME-FORMAT`

- [ ] **Step 3: Update `_validate_name()` and add namespace info**

In `skillctl/validator.py`, change `NAME_PATTERN` and `_validate_name()`:

```python
# Replace the existing NAME_PATTERN (line 16):
NAME_PATTERN = re.compile(r"^[a-z0-9-]+/[a-z0-9-]+$")

# With two patterns:
NAMESPACED_NAME_PATTERN = re.compile(r"^[a-z0-9-]+/[a-z0-9-]+$")
BARE_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
```

Replace `_validate_name()`:

```python
def _validate_name(self, name: str) -> tuple[Optional[ValidationIssue], Optional[ValidationIssue]]:
    """Validate name format. Returns (error_or_None, info_or_None)."""
    if not name:
        return None, None  # Empty name is caught by _validate_structure

    if NAMESPACED_NAME_PATTERN.match(name):
        return None, None  # Fully qualified, all good

    if BARE_NAME_PATTERN.match(name):
        info = ValidationIssue(
            code="VAL-NAME-NO-NAMESPACE",
            message=f"Name '{name}' has no namespace — add 'skillctl.namespace' in frontmatter or use skill.yaml to publish to registry",
            path="metadata.name",
            hint="Use format like 'my-org/my-skill' for registry publishing",
            severity="info",
        )
        return None, info

    return ValidationIssue(
        code="VAL-NAME-FORMAT",
        message=f"Name '{name}' must be lowercase with hyphens (e.g., 'my-skill' or 'my-org/my-skill')",
        path="metadata.name",
        hint="Use lowercase letters, numbers, and hyphens only",
    ), None
```

Update `validate()` to handle the new tuple return from `_validate_name()`:

```python
# Replace (lines 75-77):
#     name_issue = self._validate_name(manifest.metadata.name)
#     if name_issue:
#         errors.append(name_issue)

# With:
name_error, name_info = self._validate_name(manifest.metadata.name)
if name_error:
    errors.append(name_error)
if name_info:
    warnings.append(name_info)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_validator.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add skillctl/validator.py tests/test_validator.py
git commit -m "feat: allow bare skill names, add VAL-NAME-NO-NAMESPACE info"
```

---

### Task 3: Namespace gate in cmd_apply

**Files:**
- Modify: `skillctl/cli.py`
- Test: `tests/test_cli_smoke.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli_smoke.py`:

```python
class TestApplyNamespaceGate:
    def test_apply_bare_name_blocked(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: bare-name\ndescription: test\n---\n\nBody")
        r = _run(["apply", "--local", str(skill_dir)])
        assert r.returncode != 0
        assert "namespace" in r.stderr.lower()

    def test_apply_namespaced_name_works(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: code-reviewer\ndescription: test\nskillctl:\n  namespace: test-org\n  version: 0.1.0\n---\n\nBody")
        r = _run(["apply", "--local", "--dry-run", str(skill_dir)])
        assert r.returncode == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_cli_smoke.py::TestApplyNamespaceGate -v`
Expected: FAIL — bare name currently passes apply

- [ ] **Step 3: Add namespace gate to cmd_apply**

In `skillctl/cli.py`, in `cmd_apply()`, after the validation check (after line 452) and before content resolution (line 454), add:

```python
    # Namespace gate — bare names cannot be stored/published
    if "/" not in manifest.metadata.name:
        raise SkillctlError(
            code="E_NO_NAMESPACE",
            what=f"Skill '{manifest.metadata.name}' has no namespace",
            why="The store and registry require namespaced names to prevent collisions",
            fix="Add 'skillctl:\\n  namespace: my-org' to SKILL.md frontmatter, or create a skill.yaml",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_cli_smoke.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add skillctl/cli.py tests/test_cli_smoke.py
git commit -m "feat: gate apply on namespace — bare names blocked from store/registry"
```

---

### Task 4: Deduplicate frontmatter parser in install.py

**Files:**
- Modify: `skillctl/install.py`
- Test: `tests/test_install.py`

- [ ] **Step 1: Replace local parser with shared import**

In `skillctl/install.py`, replace the local `_parse_skill_frontmatter()` function (lines 364-375) with an import:

```python
# Add to imports at top of file:
from skillctl.manifest import _parse_frontmatter

# Remove the entire _parse_skill_frontmatter function (lines 364-375)

# In install_skill(), replace the call (around line 403):
#     frontmatter, body = _parse_skill_frontmatter(skill_content)
# With:
    frontmatter, body = _parse_frontmatter(skill_content)
```

- [ ] **Step 2: Run install tests to verify no regression**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_install.py -v`
Expected: All 42 pass (no behavior change)

- [ ] **Step 3: Run full suite**

Run: `PYTHONPATH=. .venv/bin/pytest tests/ --ignore=tests/test_github_backend.py -m "not integration" -q`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add skillctl/install.py
git commit -m "refactor: deduplicate frontmatter parser — install.py uses shared _parse_frontmatter"
```

---

### Task 5: Documentation and final verification

**Files:**
- Modify: `docs/REFERENCE.md`, `README.md`, `AGENTS.md`

- [ ] **Step 1: Add SKILL.md format section to REFERENCE.md**

Add after the existing "Backward compatibility" section (before "## CLI Reference"):

```markdown
### SKILL.md format

A SKILL.md file with YAML frontmatter is a fully valid skill definition. No companion `skill.yaml` is required for local operations (validate, eval, install).

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

| Field | Required | Description |
|-------|----------|-------------|
| `name` | No | Skill name (default: directory name) |
| `description` | No | What the skill does (recommended) |
| `allowed-tools` | No | Claude Code tool permissions (passthrough) |
| `paths` | No | File glob patterns for activation |
| `disable-model-invocation` | No | Prevent auto-invocation by IDE |
| `skillctl.namespace` | For `apply` | Governance namespace (e.g., `my-org`) |
| `skillctl.version` | No | Semver version (default: `0.1.0`) |
| `skillctl.category` | No | Skill category (see Known Categories) |
| `skillctl.tags` | No | Discovery tags |
| `skillctl.capabilities` | No | Declared tool capabilities |

The `skillctl:` block is ignored by all IDEs (unknown YAML keys are silently skipped). Standard fields (`name`, `description`, `paths`, `allowed-tools`) are read by IDEs natively.

When both `skill.yaml` and `SKILL.md` exist, `skill.yaml` takes precedence.
```

- [ ] **Step 2: Update README.md**

Add a line to the quickstart section after the create/validate/eval/apply block:

```markdown
# Works with existing IDE skills — no skill.yaml needed
skillctl validate ~/.claude/skills/my-skill/SKILL.md
skillctl eval audit ~/.claude/skills/my-skill/
```

- [ ] **Step 3: Run full CI checks locally**

```bash
PYTHONPATH=. .venv/bin/pytest tests/ --ignore=tests/test_github_backend.py -m "not integration" -q
.venv/bin/ruff check skillctl/ plugin/ tests/
.venv/bin/ruff format --check skillctl/ plugin/ tests/
.venv/bin/pyright skillctl/manifest.py skillctl/validator.py skillctl/install.py --pythonversion 3.10
```

Expected: All pass, 0 errors

- [ ] **Step 4: Commit and push**

```bash
git add docs/REFERENCE.md README.md
git commit -m "docs: add SKILL.md format section with skillctl: governance block"
git push
```
