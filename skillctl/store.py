"""Content-addressed storage for validated skills."""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from skillctl.errors import SkillctlError
from skillctl.manifest import SkillManifest

DEFAULT_STORE_ROOT = Path.home() / ".skillctl"


@dataclass
class IndexEntry:
    name: str
    version: str
    hash: str
    tags: list[str]
    pushed_at: str  # ISO 8601
    size: int


@dataclass
class PushResult:
    hash: str
    path: str
    size: int
    created: bool  # True if new, False if already existed


class ContentStore:
    """Content-addressed storage for validated skills."""

    def __init__(self, root: Path = DEFAULT_STORE_ROOT):
        self.root = root
        self.store_dir = root / "store"
        self.index_path = root / "index.json"

    def push(
        self,
        manifest: SkillManifest,
        content: bytes,
        dry_run: bool = False,
    ) -> PushResult:
        """Store a validated skill. Returns PushResult."""
        content_hash = hashlib.sha256(content).hexdigest()
        prefix = content_hash[:2]
        store_path = self.store_dir / prefix / content_hash

        if dry_run:
            return PushResult(
                hash=content_hash,
                path=str(store_path),
                size=len(content),
                created=not store_path.exists(),
            )

        # Check for duplicate version (same name@version)
        index = self._load_index()
        existing = self._find_entry(index, manifest.metadata.name, manifest.metadata.version)
        if existing:
            raise SkillctlError(
                code="E_ALREADY_EXISTS",
                what=f"{manifest.metadata.name}@{manifest.metadata.version} already in store",
                why="Pushing the same version twice could mask changes",
                fix="Bump the version in skill.yaml, or remove the old one first",
            )

        # Write content (atomic: write to temp, rename)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=store_path.parent)
        try:
            os.write(tmp_fd, content)
            os.close(tmp_fd)
            os.rename(tmp_path, str(store_path))
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

        # Write manifest alongside content
        manifest_path = self.store_dir / prefix / f"{content_hash}.manifest.yaml"
        self._write_manifest(manifest_path, manifest)

        # Update index (atomic)
        entry = IndexEntry(
            name=manifest.metadata.name,
            version=manifest.metadata.version,
            hash=content_hash,
            tags=manifest.metadata.tags,
            pushed_at=datetime.now(timezone.utc).isoformat(),
            size=len(content),
        )
        index.append(entry)
        self._save_index(index)

        return PushResult(
            hash=content_hash,
            path=str(store_path),
            size=len(content),
            created=True,
        )

    def pull(self, name: str, version: str) -> tuple[bytes, dict]:
        """Retrieve skill content and manifest by name@version."""
        index = self._load_index()
        entry = self._find_entry(index, name, version)
        if not entry:
            raise SkillctlError(
                code="E_NOT_FOUND",
                what=f"{name}@{version} not found in local store",
                why="The skill must be pushed before it can be pulled",
                fix="Run 'skillctl push' first, or check 'skillctl list'",
            )

        prefix = entry.hash[:2]
        content_path = self.store_dir / prefix / entry.hash
        content = content_path.read_bytes()

        # Verify integrity
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != entry.hash:
            raise SkillctlError(
                code="E_INTEGRITY",
                what=f"Hash mismatch for {name}@{version}",
                why="Stored content has been corrupted or tampered with",
                fix="Re-push the skill to repair the store",
            )

        return content, entry.__dict__

    def list_skills(
        self, namespace: str = None, tag: str = None
    ) -> list[IndexEntry]:
        """List skills in the store, optionally filtered."""
        index = self._load_index()
        results = index

        if namespace:
            results = [e for e in results if e.name.startswith(f"{namespace}/")]
        if tag:
            results = [e for e in results if tag in e.tags]

        return sorted(results, key=lambda e: (e.name, e.version))

    def delete_skill(self, name: str, version: str) -> None:
        """Remove a skill version from the local store."""
        index = self._load_index()
        entry = self._find_entry(index, name, version)
        if not entry:
            raise SkillctlError(
                code="E_NOT_FOUND",
                what=f"{name}@{version} not found in local store",
                why="Cannot delete a skill that doesn't exist",
                fix="Check 'skillctl get skills' for available skills",
            )

        prefix = entry.hash[:2]
        content_path = self.store_dir / prefix / entry.hash
        manifest_path = self.store_dir / prefix / f"{entry.hash}.manifest.yaml"

        # Remove content file
        if content_path.exists():
            content_path.unlink()
        # Remove manifest file
        if manifest_path.exists():
            manifest_path.unlink()

        # Remove entry from index
        index = [e for e in index if not (e.name == name and e.version == version)]
        self._save_index(index)

    def list_versions(self, name: str) -> list[IndexEntry]:
        """List all versions of a skill by name."""
        index = self._load_index()
        return sorted(
            [e for e in index if e.name == name],
            key=lambda e: e.version,
            reverse=True,
        )

    def _load_index(self) -> list[IndexEntry]:
        if not self.index_path.exists():
            return []
        with open(self.index_path) as f:
            raw = json.load(f)
        return [IndexEntry(**e) for e in raw]

    def _save_index(self, index: list[IndexEntry]):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self.root)
        try:
            data = json.dumps([e.__dict__ for e in index], indent=2)
            os.write(tmp_fd, data.encode())
            os.close(tmp_fd)
            os.rename(tmp_path, str(self.index_path))
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

    def _find_entry(
        self, index: list[IndexEntry], name: str, version: str
    ) -> IndexEntry | None:
        for entry in index:
            if entry.name == name and entry.version == version:
                return entry
        return None

    def _write_manifest(self, path: Path, manifest: SkillManifest):
        """Write manifest YAML alongside stored content."""
        data = {
            "apiVersion": manifest.api_version,
            "kind": manifest.kind,
            "metadata": {
                "name": manifest.metadata.name,
                "version": manifest.metadata.version,
                "description": manifest.metadata.description,
            },
            "spec": {
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.type,
                        **({"default": p.default} if p.default else {}),
                        **({"values": p.values} if p.values else {}),
                    }
                    for p in manifest.spec.parameters
                ],
                "capabilities": list(manifest.spec.capabilities),
            },
        }
        with open(path, "w") as f:
            yaml.safe_dump(data, f)
