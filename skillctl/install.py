"""Install skills to AI coding IDEs — Claude Code, Cursor, Windsurf, Copilot, Kiro."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from skillctl.errors import SkillctlError
from skillctl.manifest import _parse_frontmatter
from skillctl.store import ContentStore
from skillctl.utils import parse_ref

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
        self._lock_fd = None
        self._data: dict[str, dict[str, InstallRecord]] = {}
        self._acquire_lock()
        self._load()

    def _acquire_lock(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_path.with_suffix(".lock")
        self._lock_fd = open(lock_path, "w")  # noqa: SIM115
        fcntl.flock(self._lock_fd, fcntl.LOCK_EX)

    def _release_lock(self):
        if self._lock_fd:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            self._lock_fd.close()
            self._lock_fd = None

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
        except OSError as e:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise SkillctlError(
                code="E_STATE_WRITE",
                what="Failed to write installation state",
                why=str(e),
                fix="Check disk space and permissions on ~/.skillctl/",
            ) from e
        finally:
            self._release_lock()

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


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------


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


TARGET_ALIASES: dict[str, str] = {
    "claude-code": "claude",
}


def _resolve_targets(targets: list[str], global_scope: bool) -> list[str]:
    resolved = []
    for t in targets:
        if t == "all":
            resolved.extend(detect_targets(global_scope))
        else:
            canonical = TARGET_ALIASES.get(t, t)
            if canonical in TARGETS:
                resolved.append(canonical)
            else:
                all_names = sorted(set(list(TARGETS.keys()) + list(TARGET_ALIASES.keys())))
                raise SkillctlError(
                    code="E_TARGET_NOT_FOUND",
                    what=f"Unknown target: {t}",
                    why=f"Valid targets are: {', '.join(all_names)}, all",
                    fix=f"Use one of: {', '.join(all_names)}, all",
                )
    return sorted(set(resolved))


def install_skill(
    ref: str,
    targets: list[str],
    global_scope: bool = False,
    force: bool = False,
    store: ContentStore | None = None,
    tracker_path: Path = DEFAULT_STATE_PATH,
    dry_run: bool = False,
) -> list[InstallResult]:
    """Install a skill from the store to one or more IDE targets."""
    if store is None:
        store = ContentStore()

    name, version = parse_ref(ref)
    content_bytes, entry = store.pull(name, version)
    skill_content = content_bytes.decode("utf-8", errors="replace")

    if not skill_content.strip():
        raise SkillctlError(
            code="E_EMPTY_CONTENT",
            what=f"Skill {ref} has empty content",
            why="Cannot install a skill with no content to IDE targets",
            fix="Check the skill's SKILL.md or inline content in skill.yaml",
        )

    frontmatter, body = _parse_frontmatter(skill_content)

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
            results.append(
                InstallResult(
                    target=target_name,
                    success=False,
                    path=str(target_path),
                    message="File was modified externally. Use --force to overwrite.",
                )
            )
            continue

        formatted = cfg.format_fn(skill_basename, frontmatter, body)
        content_hash = hashlib.sha256(formatted.encode()).hexdigest()

        if dry_run:
            results.append(
                InstallResult(
                    target=target_name,
                    success=True,
                    path=str(target_path),
                    message=f"[dry-run] Would install to {target_path}",
                )
            )
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(formatted)

        record = InstallRecord(
            path=str(target_path),
            scope="global" if global_scope else "project",
            installed_at=datetime.now(timezone.utc).isoformat(),
            content_hash=content_hash,
        )
        tracker.add(ref, target_name, record)
        results.append(
            InstallResult(
                target=target_name,
                success=True,
                path=str(target_path),
                message=f"Installed to {target_path}",
            )
        )

    if not dry_run:
        tracker.save()
    else:
        tracker._release_lock()
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
            results.append(
                UninstallResult(
                    target=target_name,
                    success=False,
                    path="",
                    message=f"No installation tracked for {ref} in {target_name}",
                )
            )
            continue

        path = Path(record.path)
        if tracker.is_modified(record):
            print(f"Warning: {path} was modified since installation", file=sys.stderr)

        if path.exists():
            path.unlink()
            if path.parent.is_dir() and not any(path.parent.iterdir()):
                path.parent.rmdir()

        tracker.remove(ref, target_name)
        results.append(
            UninstallResult(
                target=target_name,
                success=True,
                path=str(path),
                message=f"Uninstalled from {path}",
            )
        )

    tracker.save()
    return results


def download_skill(url: str, target_dir: Path) -> Path:
    """Download a SKILL.md from a URL to a local directory. Returns the path."""
    import re
    import urllib.request
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise SkillctlError(
            code="E_INVALID_URL",
            what=f"Unsupported URL scheme: {parsed.scheme}",
            why="Only https:// and http:// URLs are supported for security",
            fix="Use an https:// URL",
        )

    content = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
    skill_name = "downloaded-skill"
    frontmatter, body = _parse_frontmatter(content)
    if frontmatter.get("name"):
        skill_name = frontmatter["name"]
    # Sanitize: only allow alphanumeric, hyphens
    skill_name = re.sub(r"[^a-z0-9-]", "-", skill_name.lower())
    if not skill_name or skill_name == "-":
        skill_name = "downloaded-skill"
    skill_dir = target_dir / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content)
    return skill_dir


def list_installations(
    target: str | None = None,
    tracker_path: Path = DEFAULT_STATE_PATH,
) -> dict:
    """List all tracked installations."""
    tracker = InstallationTracker(state_path=tracker_path)
    if target:
        return tracker.list_by_target(target)
    return tracker.list_all()
