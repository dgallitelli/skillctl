# Install Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `skillctl install` / `uninstall` / `get installations` commands that distribute governed skills to Claude Code, Cursor, Windsurf, GitHub Copilot, and Kiro.

**Architecture:** A single new module `skillctl/install.py` handles target registry, frontmatter translation, file operations, and installation tracking. CLI commands in `cli.py` are thin wrappers. One new MCP tool in the plugin.

**Tech Stack:** Python 3.10+, pyyaml, existing skillctl modules (store, manifest, errors, utils)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `skillctl/install.py` | Create | Target registry, frontmatter translation, install/uninstall/list/detect |
| `skillctl/cli.py` | Modify | Add install/uninstall parsers + dispatch, extend `get` with `installations` |
| `plugin/scripts/mcp_server.py` | Modify | Add `skillctl_install` tool |
| `tests/test_install.py` | Create | Unit tests for all install.py functions |
| `tests/test_cli_smoke.py` | Modify | Add smoke tests for new CLI commands |

---

### Task 1: Data types and installation tracker

**Files:**
- Create: `skillctl/install.py`
- Test: `tests/test_install.py`

- [ ] **Step 1: Write failing tests for data types and tracker**

```python
# tests/test_install.py
"""Tests for the skill install module."""

import hashlib
import json
from pathlib import Path

from skillctl.install import (
    InstallRecord,
    InstallationTracker,
)


class TestInstallRecord:
    def test_to_dict(self):
        rec = InstallRecord(
            path="/tmp/.claude/skills/my-skill/SKILL.md",
            scope="project",
            installed_at="2026-04-27T15:00:00Z",
            content_hash="abc123",
        )
        d = rec.to_dict()
        assert d["path"] == "/tmp/.claude/skills/my-skill/SKILL.md"
        assert d["scope"] == "project"
        assert d["content_hash"] == "abc123"

    def test_from_dict(self):
        rec = InstallRecord.from_dict({
            "path": "/tmp/x",
            "scope": "global",
            "installed_at": "2026-01-01T00:00:00Z",
            "content_hash": "def456",
        })
        assert rec.path == "/tmp/x"
        assert rec.scope == "global"


class TestInstallationTracker:
    def test_empty_tracker(self, tmp_path):
        tracker = InstallationTracker(state_path=tmp_path / "installations.json")
        assert tracker.list_all() == {}

    def test_add_and_list(self, tmp_path):
        tracker = InstallationTracker(state_path=tmp_path / "installations.json")
        rec = InstallRecord(
            path="/tmp/x",
            scope="project",
            installed_at="2026-01-01T00:00:00Z",
            content_hash="abc",
        )
        tracker.add("my-org/my-skill@1.0.0", "claude", rec)
        tracker.save()

        tracker2 = InstallationTracker(state_path=tmp_path / "installations.json")
        entries = tracker2.list_all()
        assert "my-org/my-skill@1.0.0" in entries
        assert "claude" in entries["my-org/my-skill@1.0.0"]

    def test_remove(self, tmp_path):
        tracker = InstallationTracker(state_path=tmp_path / "installations.json")
        rec = InstallRecord(path="/tmp/x", scope="project", installed_at="t", content_hash="h")
        tracker.add("ref@1.0", "cursor", rec)
        tracker.save()
        tracker.remove("ref@1.0", "cursor")
        tracker.save()

        tracker2 = InstallationTracker(state_path=tmp_path / "installations.json")
        assert tracker2.list_all() == {}

    def test_is_modified(self, tmp_path):
        target_file = tmp_path / "skill.md"
        content = "# Hello"
        target_file.write_text(content)
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        tracker = InstallationTracker(state_path=tmp_path / "installations.json")
        rec = InstallRecord(path=str(target_file), scope="project", installed_at="t", content_hash=content_hash)
        assert not tracker.is_modified(rec)

        target_file.write_text("# Modified")
        assert tracker.is_modified(rec)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_install.py -v`
Expected: ImportError — `skillctl.install` does not exist yet

- [ ] **Step 3: Implement data types and tracker**

```python
# skillctl/install.py
"""Install skills to AI coding IDEs — Claude Code, Cursor, Windsurf, Copilot, Kiro."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from skillctl.errors import SkillctlError

DEFAULT_STATE_PATH = Path.home() / ".skillctl" / "installations.json"


@dataclass
class InstallRecord:
    path: str
    scope: str  # "project" or "global"
    installed_at: str
    content_hash: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> InstallRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class InstallationTracker:
    """Track which skills are installed where."""

    def __init__(self, state_path: Path = DEFAULT_STATE_PATH):
        self.state_path = state_path
        self._data: dict[str, dict[str, InstallRecord]] = {}
        self._load()

    def _load(self):
        if self.state_path.exists():
            raw = json.loads(self.state_path.read_text())
            for ref, targets in raw.items():
                self._data[ref] = {t: InstallRecord.from_dict(r) for t, r in targets.items()}

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {ref: {t: r.to_dict() for t, r in targets.items()} for ref, targets in self._data.items()}
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self.state_path.parent)
        try:
            os.write(tmp_fd, json.dumps(data, indent=2).encode())
            os.close(tmp_fd)
            os.replace(tmp_path, str(self.state_path))
        except Exception:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def add(self, ref: str, target: str, record: InstallRecord):
        if ref not in self._data:
            self._data[ref] = {}
        self._data[ref][target] = record

    def remove(self, ref: str, target: str):
        if ref in self._data:
            self._data[ref].pop(target, None)
            if not self._data[ref]:
                del self._data[ref]

    def get(self, ref: str, target: str) -> InstallRecord | None:
        return self._data.get(ref, {}).get(target)

    def list_all(self) -> dict[str, dict[str, InstallRecord]]:
        return self._data

    def list_by_target(self, target: str) -> dict[str, InstallRecord]:
        result = {}
        for ref, targets in self._data.items():
            if target in targets:
                result[ref] = targets[target]
        return result

    @staticmethod
    def is_modified(record: InstallRecord) -> bool:
        path = Path(record.path)
        if not path.exists():
            return True
        current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        return current_hash != record.content_hash
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_install.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add skillctl/install.py tests/test_install.py
git commit -m "feat(install): add data types and installation tracker"
```

---

### Task 2: Frontmatter translation for all 5 targets

**Files:**
- Modify: `skillctl/install.py`
- Test: `tests/test_install.py`

- [ ] **Step 1: Write failing tests for frontmatter translation**

Append to `tests/test_install.py`:

```python
from skillctl.install import (
    format_for_claude,
    format_for_cursor,
    format_for_windsurf,
    format_for_copilot,
    format_for_kiro,
)


class TestFormatForClaude:
    def test_passthrough(self):
        result = format_for_claude("my-skill", {"description": "Does stuff", "allowed-tools": "Read"}, "# Body")
        assert "description: Does stuff" in result
        assert "allowed-tools: Read" in result
        assert "# Body" in result

    def test_wraps_in_frontmatter(self):
        result = format_for_claude("s", {"description": "d"}, "body")
        assert result.startswith("---\n")
        assert "\n---\n" in result


class TestFormatForCursor:
    def test_basic(self):
        result = format_for_cursor("my-skill", {"description": "Does stuff"}, "# Body")
        assert "description: Does stuff" in result
        assert "alwaysApply: true" in result
        assert "# Body" in result

    def test_with_paths(self):
        result = format_for_cursor("s", {"description": "d", "paths": "**/*.py"}, "body")
        assert "globs:" in result
        assert "**/*.py" in result

    def test_disable_model_invocation(self):
        result = format_for_cursor("s", {"description": "d", "disable-model-invocation": True}, "body")
        assert "alwaysApply: false" in result

    def test_drops_allowed_tools(self, capsys):
        result = format_for_cursor("s", {"description": "d", "allowed-tools": "Bash(*)"}, "body")
        assert "allowed-tools" not in result
        assert "allowed-tools" in capsys.readouterr().err


class TestFormatForWindsurf:
    def test_basic_always_on(self):
        result = format_for_windsurf("s", {"description": "d"}, "body")
        assert "trigger: always_on" in result

    def test_with_paths(self):
        result = format_for_windsurf("s", {"description": "d", "paths": "**/*.py"}, "body")
        assert "trigger: glob" in result

    def test_manual(self):
        result = format_for_windsurf("s", {"description": "d", "disable-model-invocation": True}, "body")
        assert "trigger: manual" in result

    def test_model_decision(self):
        result = format_for_windsurf("s", {"description": "d", "disable-model-invocation": False}, "body")
        assert "trigger: model_decision" in result


class TestFormatForCopilot:
    def test_basic_no_frontmatter(self):
        result = format_for_copilot("s", {"description": "d"}, "# Body")
        assert "# Body" in result
        assert "---" not in result

    def test_with_paths(self):
        result = format_for_copilot("s", {"description": "d", "paths": "**/*.py"}, "body")
        assert "applyTo:" in result
        assert "**/*.py" in result


class TestFormatForKiro:
    def test_basic_always(self):
        result = format_for_kiro("my-skill", {"description": "Does stuff"}, "body")
        assert "inclusion: always" in result
        assert "name: my-skill" in result

    def test_with_paths(self):
        result = format_for_kiro("s", {"description": "d", "paths": "**/*.py"}, "body")
        assert "inclusion: fileMatch" in result
        assert "fileMatchPattern" in result

    def test_manual(self):
        result = format_for_kiro("s", {"description": "d", "disable-model-invocation": True}, "body")
        assert "inclusion: manual" in result

    def test_auto(self):
        result = format_for_kiro("s", {"description": "d", "disable-model-invocation": False}, "body")
        assert "inclusion: auto" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_install.py::TestFormatForClaude -v`
Expected: ImportError — functions not defined yet

- [ ] **Step 3: Implement format functions**

Add to `skillctl/install.py`:

```python
import yaml


# ---------------------------------------------------------------------------
# Frontmatter translation
# ---------------------------------------------------------------------------

_CLAUDE_ONLY_FIELDS = {"allowed-tools", "context", "model", "agent", "effort", "hooks", "shell"}


def _warn_dropped(field: str, target: str):
    print(f"Warning: '{field}' not supported by {target}, skipping", file=sys.stderr)


def _emit_frontmatter(fields: dict) -> str:
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - \"{item}\"")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def format_for_claude(name: str, frontmatter: dict, body: str) -> str:
    fm = dict(frontmatter)
    return f"{_emit_frontmatter(fm)}\n\n{body}\n"


def format_for_cursor(name: str, frontmatter: dict, body: str) -> str:
    fm: dict = {}
    for k, v in frontmatter.items():
        if k in _CLAUDE_ONLY_FIELDS:
            _warn_dropped(k, "cursor")
            continue
        if k == "description":
            fm["description"] = v
        elif k == "paths":
            fm["globs"] = v if isinstance(v, list) else [v]
        elif k == "disable-model-invocation":
            fm["alwaysApply"] = False
        else:
            fm[k] = v

    if "alwaysApply" not in fm and "globs" not in fm:
        fm["alwaysApply"] = True
    elif "globs" in fm and "alwaysApply" not in fm:
        fm["alwaysApply"] = False

    return f"{_emit_frontmatter(fm)}\n\n{body}\n"


def format_for_windsurf(name: str, frontmatter: dict, body: str) -> str:
    fm: dict = {}
    for k, v in frontmatter.items():
        if k in _CLAUDE_ONLY_FIELDS:
            _warn_dropped(k, "windsurf")
            continue
        if k == "description":
            fm["description"] = v
        elif k == "paths":
            fm["trigger"] = "glob"
            fm["globs"] = v if isinstance(v, list) else [v]
        elif k == "disable-model-invocation":
            fm["trigger"] = "manual" if v else "model_decision"
        else:
            fm[k] = v

    if "trigger" not in fm:
        fm["trigger"] = "always_on"

    return f"{_emit_frontmatter(fm)}\n\n{body}\n"


def format_for_copilot(name: str, frontmatter: dict, body: str) -> str:
    for k in _CLAUDE_ONLY_FIELDS:
        if k in frontmatter:
            _warn_dropped(k, "copilot")

    paths = frontmatter.get("paths")
    if paths:
        apply_to = paths if isinstance(paths, str) else ",".join(paths)
        fm = {"applyTo": f'"{apply_to}"'}
        return f"{_emit_frontmatter(fm)}\n\n{body}\n"

    return f"{body}\n"


def format_for_kiro(name: str, frontmatter: dict, body: str) -> str:
    fm: dict = {"name": name}
    for k, v in frontmatter.items():
        if k in _CLAUDE_ONLY_FIELDS:
            _warn_dropped(k, "kiro")
            continue
        if k == "description":
            fm["description"] = v
        elif k == "paths":
            fm["inclusion"] = "fileMatch"
            pattern = v if isinstance(v, str) else ",".join(v)
            fm["fileMatchPattern"] = f'"{pattern}"'
        elif k == "disable-model-invocation":
            fm["inclusion"] = "manual" if v else "auto"
        else:
            fm[k] = v

    if "inclusion" not in fm:
        fm["inclusion"] = "always"

    return f"{_emit_frontmatter(fm)}\n\n{body}\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_install.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add skillctl/install.py tests/test_install.py
git commit -m "feat(install): add frontmatter translation for 5 IDE targets"
```

---

### Task 3: Target registry and detect_targets

**Files:**
- Modify: `skillctl/install.py`
- Test: `tests/test_install.py`

- [ ] **Step 1: Write failing tests for target registry and detection**

Append to `tests/test_install.py`:

```python
from skillctl.install import TARGETS, detect_targets


class TestTargetRegistry:
    def test_all_targets_present(self):
        assert set(TARGETS.keys()) == {"claude", "cursor", "windsurf", "copilot", "kiro"}

    def test_each_target_has_format_fn(self):
        for name, cfg in TARGETS.items():
            assert callable(cfg.format_fn), f"{name} missing format_fn"

    def test_each_target_has_project_path(self):
        for name, cfg in TARGETS.items():
            path = cfg.project_path("test-skill")
            assert isinstance(path, Path)
            assert "test-skill" in str(path)

    def test_claude_creates_directory(self):
        path = TARGETS["claude"].project_path("my-skill")
        assert path.name == "SKILL.md"
        assert path.parent.name == "my-skill"

    def test_cursor_flat_file(self):
        path = TARGETS["cursor"].project_path("my-skill")
        assert path.suffix == ".mdc"

    def test_copilot_instructions_suffix(self):
        path = TARGETS["copilot"].project_path("my-skill")
        assert path.name == "my-skill.instructions.md"

    def test_global_path_none_for_cursor(self):
        assert TARGETS["cursor"].global_path is None

    def test_global_path_none_for_copilot(self):
        assert TARGETS["copilot"].global_path is None

    def test_global_path_exists_for_claude(self):
        assert TARGETS["claude"].global_path is not None


class TestDetectTargets:
    def test_detects_claude(self, tmp_path, monkeypatch):
        (tmp_path / ".claude").mkdir()
        monkeypatch.chdir(tmp_path)
        detected = detect_targets(global_scope=False)
        assert "claude" in detected

    def test_no_targets(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        detected = detect_targets(global_scope=False)
        assert detected == []

    def test_multiple_targets(self, tmp_path, monkeypatch):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".cursor").mkdir()
        (tmp_path / ".kiro").mkdir()
        monkeypatch.chdir(tmp_path)
        detected = detect_targets(global_scope=False)
        assert set(detected) == {"claude", "cursor", "kiro"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_install.py::TestTargetRegistry -v`
Expected: ImportError

- [ ] **Step 3: Implement target registry and detect_targets**

Add to `skillctl/install.py`:

```python
@dataclass
class TargetConfig:
    name: str
    project_path_fn: Callable[[str], Path]
    global_path_fn: Callable[[str], Path] | None
    format_fn: Callable[[str, dict, str], str]
    detect_dir: str

    def project_path(self, skill_name: str) -> Path:
        return self.project_path_fn(skill_name)

    @property
    def global_path(self) -> Callable[[str], Path] | None:
        return self.global_path_fn


def _skill_basename(name: str) -> str:
    """Extract the short name from 'namespace/skill-name' or 'skill-name'."""
    return name.split("/")[-1] if "/" in name else name


TARGETS: dict[str, TargetConfig] = {
    "claude": TargetConfig(
        name="claude",
        project_path_fn=lambda n: Path(".claude/skills") / _skill_basename(n) / "SKILL.md",
        global_path_fn=lambda n: Path.home() / ".claude/skills" / _skill_basename(n) / "SKILL.md",
        format_fn=format_for_claude,
        detect_dir=".claude",
    ),
    "cursor": TargetConfig(
        name="cursor",
        project_path_fn=lambda n: Path(".cursor/rules") / f"{_skill_basename(n)}.mdc",
        global_path_fn=None,
        format_fn=format_for_cursor,
        detect_dir=".cursor",
    ),
    "windsurf": TargetConfig(
        name="windsurf",
        project_path_fn=lambda n: Path(".windsurf/rules") / f"{_skill_basename(n)}.md",
        global_path_fn=lambda n: Path.home() / ".codeium/windsurf/memories/global_rules.md",
        format_fn=format_for_windsurf,
        detect_dir=".windsurf",
    ),
    "copilot": TargetConfig(
        name="copilot",
        project_path_fn=lambda n: Path(".github/instructions") / f"{_skill_basename(n)}.instructions.md",
        global_path_fn=None,
        format_fn=format_for_copilot,
        detect_dir=".github",
    ),
    "kiro": TargetConfig(
        name="kiro",
        project_path_fn=lambda n: Path(".kiro/steering") / f"{_skill_basename(n)}.md",
        global_path_fn=lambda n: Path.home() / ".kiro/steering" / f"{_skill_basename(n)}.md",
        format_fn=format_for_kiro,
        detect_dir=".kiro",
    ),
}


def detect_targets(global_scope: bool = False) -> list[str]:
    """Auto-detect which IDE targets are present."""
    detected = []
    for name, cfg in TARGETS.items():
        if global_scope:
            if cfg.global_path_fn is not None:
                detected.append(name)
        else:
            if Path(cfg.detect_dir).is_dir():
                detected.append(name)
    return sorted(detected)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_install.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add skillctl/install.py tests/test_install.py
git commit -m "feat(install): add target registry and auto-detection"
```

---

### Task 4: install_skill and uninstall_skill

**Files:**
- Modify: `skillctl/install.py`
- Test: `tests/test_install.py`

- [ ] **Step 1: Write failing tests for install and uninstall**

Append to `tests/test_install.py`:

```python
from skillctl.install import install_skill, uninstall_skill, InstallResult, UninstallResult
from skillctl.store import ContentStore
from skillctl.manifest import ManifestLoader
from skillctl.validator import SchemaValidator


def _create_stored_skill(tmp_path: Path) -> tuple[str, ContentStore]:
    """Create a minimal skill in a temporary store and return (ref, store)."""
    store = ContentStore(root=tmp_path / "store")
    loader = ManifestLoader()
    skill_dir = tmp_path / "src"
    skill_dir.mkdir()
    (skill_dir / "skill.yaml").write_text(
        "apiVersion: skillctl.io/v1\n"
        "kind: Skill\n"
        "metadata:\n"
        "  name: test/install-test\n"
        "  version: 1.0.0\n"
        "  description: A test skill\n"
        "spec:\n"
        "  content:\n"
        "    inline: '# Test skill body'\n"
    )
    manifest, _ = loader.load(str(skill_dir / "skill.yaml"))
    content = "# Test skill body"
    store.push(manifest, content.encode(), dry_run=False)
    return "test/install-test@1.0.0", store


class TestInstallSkill:
    def test_install_to_claude(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".claude").mkdir()
        ref, store = _create_stored_skill(tmp_path)
        tracker_path = tmp_path / "installations.json"

        results = install_skill(
            ref=ref, targets=["claude"], global_scope=False, force=False,
            store=store, tracker_path=tracker_path,
        )
        assert len(results) == 1
        assert results[0].success
        installed_path = tmp_path / ".claude" / "skills" / "install-test" / "SKILL.md"
        assert installed_path.exists()
        assert "# Test skill body" in installed_path.read_text()

    def test_install_to_cursor(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".cursor").mkdir()
        ref, store = _create_stored_skill(tmp_path)
        tracker_path = tmp_path / "installations.json"

        results = install_skill(
            ref=ref, targets=["cursor"], global_scope=False, force=False,
            store=store, tracker_path=tracker_path,
        )
        assert len(results) == 1
        assert results[0].success
        installed_path = tmp_path / ".cursor" / "rules" / "install-test.mdc"
        assert installed_path.exists()

    def test_install_refuses_overwrite_without_force(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".claude").mkdir()
        ref, store = _create_stored_skill(tmp_path)
        tracker_path = tmp_path / "installations.json"

        install_skill(ref=ref, targets=["claude"], global_scope=False, force=False,
                       store=store, tracker_path=tracker_path)

        installed_path = tmp_path / ".claude" / "skills" / "install-test" / "SKILL.md"
        installed_path.write_text("# User modified this")

        results = install_skill(ref=ref, targets=["claude"], global_scope=False, force=False,
                                 store=store, tracker_path=tracker_path)
        assert not results[0].success
        assert "modified" in results[0].message.lower()

    def test_install_force_overwrites(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".claude").mkdir()
        ref, store = _create_stored_skill(tmp_path)
        tracker_path = tmp_path / "installations.json"

        install_skill(ref=ref, targets=["claude"], global_scope=False, force=False,
                       store=store, tracker_path=tracker_path)

        installed_path = tmp_path / ".claude" / "skills" / "install-test" / "SKILL.md"
        installed_path.write_text("# User modified this")

        results = install_skill(ref=ref, targets=["claude"], global_scope=False, force=True,
                                 store=store, tracker_path=tracker_path)
        assert results[0].success

    def test_invalid_target(self, tmp_path):
        ref, store = _create_stored_skill(tmp_path)
        try:
            install_skill(ref=ref, targets=["nonexistent"], global_scope=False, force=False,
                           store=store, tracker_path=tmp_path / "i.json")
            assert False, "Should have raised"
        except SkillctlError as e:
            assert e.code == "E_TARGET_NOT_FOUND"


class TestUninstallSkill:
    def test_uninstall_removes_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".claude").mkdir()
        ref, store = _create_stored_skill(tmp_path)
        tracker_path = tmp_path / "installations.json"

        install_skill(ref=ref, targets=["claude"], global_scope=False, force=False,
                       store=store, tracker_path=tracker_path)

        results = uninstall_skill(ref="test/install-test@1.0.0", targets=["claude"],
                                   tracker_path=tracker_path)
        assert len(results) == 1
        assert results[0].success
        installed_path = tmp_path / ".claude" / "skills" / "install-test" / "SKILL.md"
        assert not installed_path.exists()

    def test_uninstall_not_tracked(self, tmp_path):
        results = uninstall_skill(ref="fake/ref@1.0", targets=["claude"],
                                   tracker_path=tmp_path / "installations.json")
        assert not results[0].success
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_install.py::TestInstallSkill -v`
Expected: ImportError

- [ ] **Step 3: Implement install_skill and uninstall_skill**

Add to `skillctl/install.py`:

```python
from skillctl.store import ContentStore
from skillctl.utils import parse_ref


@dataclass
class InstallResult:
    target: str
    success: bool
    path: str
    message: str


@dataclass
class UninstallResult:
    target: str
    success: bool
    path: str
    message: str


def _resolve_targets(targets: list[str], global_scope: bool) -> list[str]:
    resolved = []
    for t in targets:
        if t == "all":
            resolved.extend(detect_targets(global_scope))
        elif t in TARGETS:
            resolved.append(t)
        else:
            raise SkillctlError(
                code="E_TARGET_NOT_FOUND",
                what=f"Unknown target: {t}",
                why=f"Valid targets are: {', '.join(TARGETS.keys())}, all",
                fix=f"Use one of: {', '.join(TARGETS.keys())}, all",
            )
    return sorted(set(resolved))


def _parse_skill_frontmatter(content: str) -> tuple[dict, str]:
    """Split SKILL.md content into frontmatter dict and body."""
    if content.startswith("---"):
        try:
            end = content.index("---", 3)
            fm_text = content[3:end].strip()
            body = content[end + 3:].strip()
            fm = yaml.safe_load(fm_text) or {}
            return fm, body
        except (ValueError, yaml.YAMLError):
            pass
    return {}, content.strip()


def install_skill(
    ref: str,
    targets: list[str],
    global_scope: bool = False,
    force: bool = False,
    store: ContentStore | None = None,
    tracker_path: Path = DEFAULT_STATE_PATH,
) -> list[InstallResult]:
    """Install a skill from the store to one or more IDE targets."""
    if store is None:
        store = ContentStore()

    name, version = parse_ref(ref)
    content_bytes, entry = store.pull(name, version)
    skill_content = content_bytes.decode("utf-8", errors="replace")
    frontmatter, body = _parse_skill_frontmatter(skill_content)

    resolved = _resolve_targets(targets, global_scope)
    tracker = InstallationTracker(state_path=tracker_path)
    results: list[InstallResult] = []
    skill_basename = _skill_basename(name)

    for target_name in resolved:
        cfg = TARGETS[target_name]

        if global_scope:
            if cfg.global_path_fn is None:
                raise SkillctlError(
                    code="E_NO_GLOBAL",
                    what=f"{target_name} does not support global installation",
                    why=f"Only project-level installation is available for {target_name}",
                    fix="Remove --global flag",
                )
            target_path = cfg.global_path_fn(name)
        else:
            target_path = Path.cwd() / cfg.project_path(name)

        existing = tracker.get(ref, target_name)
        if existing and not force and tracker.is_modified(existing):
            results.append(InstallResult(
                target=target_name, success=False, path=str(target_path),
                message=f"File was modified externally. Use --force to overwrite.",
            ))
            continue

        formatted = cfg.format_fn(skill_basename, frontmatter, body)
        content_hash = hashlib.sha256(formatted.encode()).hexdigest()

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(formatted)

        record = InstallRecord(
            path=str(target_path),
            scope="global" if global_scope else "project",
            installed_at=datetime.now(timezone.utc).isoformat(),
            content_hash=content_hash,
        )
        tracker.add(ref, target_name, record)
        results.append(InstallResult(
            target=target_name, success=True, path=str(target_path),
            message=f"Installed to {target_path}",
        ))

    tracker.save()
    return results


def uninstall_skill(
    ref: str,
    targets: list[str],
    tracker_path: Path = DEFAULT_STATE_PATH,
) -> list[UninstallResult]:
    """Remove a skill from IDE targets."""
    resolved = _resolve_targets(targets, global_scope=False)
    tracker = InstallationTracker(state_path=tracker_path)
    results: list[UninstallResult] = []

    for target_name in resolved:
        record = tracker.get(ref, target_name)
        if record is None:
            results.append(UninstallResult(
                target=target_name, success=False, path="",
                message=f"No installation tracked for {ref} in {target_name}",
            ))
            continue

        path = Path(record.path)
        if tracker.is_modified(record):
            print(f"Warning: {path} was modified since installation", file=sys.stderr)

        if path.exists():
            path.unlink()
            if path.parent.is_dir() and not any(path.parent.iterdir()):
                path.parent.rmdir()

        tracker.remove(ref, target_name)
        results.append(UninstallResult(
            target=target_name, success=True, path=str(path),
            message=f"Uninstalled from {path}",
        ))

    tracker.save()
    return results


def list_installations(
    target: str | None = None,
    tracker_path: Path = DEFAULT_STATE_PATH,
) -> dict:
    """List all tracked installations."""
    tracker = InstallationTracker(state_path=tracker_path)
    if target:
        return tracker.list_by_target(target)
    return tracker.list_all()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_install.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add skillctl/install.py tests/test_install.py
git commit -m "feat(install): add install_skill, uninstall_skill, list_installations"
```

---

### Task 5: CLI commands (install, uninstall, get installations)

**Files:**
- Modify: `skillctl/cli.py`
- Test: `tests/test_cli_smoke.py`

- [ ] **Step 1: Write failing smoke tests**

Append to `tests/test_cli_smoke.py`:

```python
class TestInstallCLI:
    def test_install_help(self):
        r = _run(["install", "--help"])
        assert r.returncode == 0
        assert "--target" in r.stdout

    def test_uninstall_help(self):
        r = _run(["uninstall", "--help"])
        assert r.returncode == 0
        assert "--target" in r.stdout

    def test_get_installations(self):
        r = _run(["get", "installations"])
        assert r.returncode == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_cli_smoke.py::TestInstallCLI -v`
Expected: FAIL — unknown command

- [ ] **Step 3: Add CLI parsers and handlers to cli.py**

Add parser registrations after the existing `search_p` block (around line 293), before the DISPATCH comment:

```python
    # skillctl install <ref-or-path> --target <targets> [--global] [--force]
    install_p = sub.add_parser("install", help="Install a skill to AI coding IDEs")
    install_p.add_argument("ref", help="Skill ref (namespace/name@version) or path to skill directory")
    install_p.add_argument("--target", required=True, help="Target IDEs (comma-separated or 'all')")
    install_p.add_argument("--global", dest="global_scope", action="store_true", help="Install to user-level directory")
    install_p.add_argument("--force", action="store_true", help="Overwrite modified files")

    # skillctl uninstall <ref> --target <targets>
    uninstall_p = sub.add_parser("uninstall", help="Remove a skill from AI coding IDEs")
    uninstall_p.add_argument("ref", help="Skill ref (namespace/name@version)")
    uninstall_p.add_argument("--target", required=True, help="Target IDEs (comma-separated or 'all')")
```

Add `installations` to the existing `get` subparser:

```python
    get_installations_p = get_sub.add_parser("installations", help="List skills installed to IDEs")
    get_installations_p.add_argument("--target", default=None, help="Filter by IDE target")
    get_installations_p.add_argument("--json", action="store_true", help="Output as JSON")
```

Add dispatch entries in the dispatch block:

```python
        elif args.command == "install":
            cmd_install(args)
        elif args.command == "uninstall":
            cmd_uninstall(args)
```

Extend `cmd_get` to handle `installations`:

```python
def cmd_get(args):
    """Dispatch 'get' subcommands."""
    if args.get_resource == "skills":
        if getattr(args, "remote", False):
            cmd_get_skills_remote(args)
        else:
            cmd_get_skills(args)
    elif args.get_resource == "skill":
        cmd_get_skill(args)
    elif args.get_resource == "installations":
        cmd_get_installations(args)
    else:
        print("Usage: skillctl get skills [--remote]", file=sys.stderr)
        print("       skillctl get skill <ref>", file=sys.stderr)
        print("       skillctl get installations [--target <ide>]", file=sys.stderr)
        sys.exit(1)
```

Add command handlers:

```python
def cmd_install(args):
    """Install a skill to AI coding IDEs."""
    from skillctl.install import install_skill
    from skillctl.store import ContentStore

    ref = args.ref
    targets = [t.strip() for t in args.target.split(",")]

    # If ref looks like a path, apply first
    if "/" in ref and "@" not in ref and Path(ref).exists():
        print(f"Applying {ref} first...")
        cmd_apply(argparse.Namespace(
            path=ref, file=None, dry_run=False, local=True,
            registry_url=None, token=None,
        ))
        loader = ManifestLoader()
        manifest, _ = loader.load(ref)
        ref = f"{manifest.metadata.name}@{manifest.metadata.version}"

    results = install_skill(
        ref=ref,
        targets=targets,
        global_scope=args.global_scope,
        force=args.force,
    )
    for r in results:
        status = "✓" if r.success else "✗"
        print(f"  {status} {r.target}: {r.message}")


def cmd_uninstall(args):
    """Uninstall a skill from AI coding IDEs."""
    from skillctl.install import uninstall_skill

    targets = [t.strip() for t in args.target.split(",")]
    results = uninstall_skill(ref=args.ref, targets=targets)
    for r in results:
        status = "✓" if r.success else "✗"
        print(f"  {status} {r.target}: {r.message}")


def cmd_get_installations(args):
    """List skills installed to IDEs."""
    from skillctl.install import list_installations

    target = getattr(args, "target", None)
    data = list_installations(target=target)

    if getattr(args, "json", False):
        import json
        serializable = {}
        for ref, targets in data.items():
            if isinstance(targets, dict):
                serializable[ref] = {t: r.to_dict() if hasattr(r, "to_dict") else r for t, r in targets.items()}
            else:
                serializable[ref] = targets.to_dict() if hasattr(targets, "to_dict") else str(targets)
        print(json.dumps(serializable, indent=2))
        return

    if not data:
        print("No installations found.")
        return

    for ref, targets in data.items():
        print(f"\n{ref}:")
        if isinstance(targets, dict):
            for t, record in targets.items():
                path = record.path if hasattr(record, "path") else record.get("path", "")
                scope = record.scope if hasattr(record, "scope") else record.get("scope", "")
                print(f"  {t}: {path} ({scope})")
        else:
            path = targets.path if hasattr(targets, "path") else ""
            print(f"  {path}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_cli_smoke.py -v`
Expected: All pass

- [ ] **Step 5: Run full test suite**

Run: `PYTHONPATH=. .venv/bin/pytest tests/ --ignore=tests/test_github_backend.py -m "not integration" -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add skillctl/cli.py tests/test_cli_smoke.py
git commit -m "feat(install): add install/uninstall/get installations CLI commands"
```

---

### Task 6: MCP server tool

**Files:**
- Modify: `plugin/scripts/mcp_server.py`

- [ ] **Step 1: Add skillctl_install tool**

Add before the `# Main` section in `plugin/scripts/mcp_server.py`:

```python
@mcp.tool()
def skillctl_install(
    ref: str,
    targets: str,
    global_scope: bool = False,
    force: bool = False,
) -> str:
    """Install a governed skill to AI coding IDEs.

    Distributes a skill from the local store to one or more IDE targets
    (Claude Code, Cursor, Windsurf, Copilot, Kiro). Translates frontmatter
    to each IDE's native format.

    Args:
        ref: Skill reference in "namespace/name@version" format.
        targets: Comma-separated IDE names or "all" (claude, cursor, windsurf, copilot, kiro).
        global_scope: Install to user-level directory instead of project-level.
        force: Overwrite files modified since last install.
    """
    try:
        from skillctl.install import install_skill

        target_list = [t.strip() for t in targets.split(",")]
        results = install_skill(
            ref=ref, targets=target_list, global_scope=global_scope, force=force,
        )
        return json.dumps({
            "results": [
                {"target": r.target, "success": r.success, "path": r.path, "message": r.message}
                for r in results
            ],
        }, indent=2)
    except Exception as e:
        return _error_response(e)
```

- [ ] **Step 2: Verify tool count is now 14**

Run: `PYTHONPATH=. .venv/bin/python -c "from plugin.scripts.mcp_server import mcp; print(len(mcp._tool_manager.list_tools()))"`
Expected: `14`

- [ ] **Step 3: Commit**

```bash
git add plugin/scripts/mcp_server.py
git commit -m "feat(install): add skillctl_install MCP tool"
```

---

### Task 7: Documentation and final verification

**Files:**
- Modify: `README.md`, `ARCHITECTURE.md`, `AGENTS.md`

- [ ] **Step 1: Update README.md**

Add to the feature table:

```markdown
| **Multi-IDE install** | Install governed skills to Claude Code, Cursor, Windsurf, Copilot, Kiro |
```

Add to the quickstart:

```bash
# Install to IDEs
skillctl install my-org/my-skill@1.0 --target all    # all detected IDEs
skillctl install my-org/my-skill@1.0 --target cursor  # specific IDE
skillctl uninstall my-org/my-skill@1.0 --target all   # remove from all
skillctl get installations                             # list what's installed where
```

- [ ] **Step 2: Update ARCHITECTURE.md**

Add `install.py` to the Core module table:

```markdown
| `install.py` | Multi-IDE skill installation. Target registry (Claude, Cursor, Windsurf, Copilot, Kiro), frontmatter translation, file operations, installation tracking. |
```

- [ ] **Step 3: Update AGENTS.md**

Add to the project structure:

```markdown
- `skillctl/install.py` — multi-IDE installer (Claude, Cursor, Windsurf, Copilot, Kiro)
```

- [ ] **Step 4: Run full CI checks locally**

```bash
PYTHONPATH=. .venv/bin/pytest tests/ --ignore=tests/test_github_backend.py -m "not integration" -q
.venv/bin/ruff check skillctl/ plugin/ tests/
.venv/bin/ruff format --check skillctl/ plugin/ tests/
.venv/bin/pyright skillctl/ --pythonversion 3.10
```

Expected: All pass, 0 errors

- [ ] **Step 5: Commit and push**

```bash
git add README.md ARCHITECTURE.md AGENTS.md
git commit -m "docs: add install command to README, ARCHITECTURE, AGENTS"
git push
```
