"""Install skills to AI coding IDEs — Claude Code, Cursor, Windsurf, Copilot, Kiro."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

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


# ---------------------------------------------------------------------------
# Frontmatter translation
# ---------------------------------------------------------------------------

_CLAUDE_ONLY_FIELDS = {
    "allowed-tools",
    "context",
    "model",
    "agent",
    "effort",
    "hooks",
    "shell",
}


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
                lines.append(f'  - "{item}"')
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


# ---------------------------------------------------------------------------
# Target registry
# ---------------------------------------------------------------------------


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
