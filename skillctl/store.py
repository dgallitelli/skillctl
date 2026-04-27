"""Content-addressed storage for validated skills."""

import hashlib
import json
import os
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
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
            os.replace(tmp_path, str(store_path))
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
                code="E_STORE_WRITE",
                what="Failed to write to skill store",
                why=str(e),
                fix="Check disk space and permissions on ~/.skillctl/",
            ) from e

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

    def list_skills(self, namespace: str | None = None, tag: str | None = None) -> list[IndexEntry]:
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

        # Remove index entry first (so a crash leaves no dangling reference)
        index = [e for e in index if not (e.name == name and e.version == version)]
        self._save_index(index)

        # Then remove files (orphaned blobs are harmless; dangling index refs are not)
        prefix = entry.hash[:2]
        content_path = self.store_dir / prefix / entry.hash
        manifest_path = self.store_dir / prefix / f"{entry.hash}.manifest.yaml"
        if content_path.exists():
            content_path.unlink()
        if manifest_path.exists():
            manifest_path.unlink()

    def list_versions(self, name: str) -> list[IndexEntry]:
        """List all versions of a skill by name."""
        index = self._load_index()
        return sorted(
            [e for e in index if e.name == name],
            key=lambda e: e.version,
            reverse=True,
        )

    def export_skills(
        self,
        output_path: Path,
        format: str = "tar.gz",
        namespace: str | None = None,
        tag: str | None = None,
    ) -> dict:
        """Export skills from the store to a portable archive.

        Returns dict with: path, format, skill_count, total_size
        """
        if format not in ("tar.gz", "zip"):
            raise SkillctlError(
                code="E_INVALID_FORMAT",
                what=f"Unsupported export format '{format}'",
                why="Only tar.gz and zip formats are supported",
                fix="Use --format tar.gz or --format zip",
            )

        entries = self.list_skills(namespace=namespace, tag=tag)

        # Build index and collect file data
        index_records: list[dict] = []
        file_entries: list[tuple[str, bytes]] = []  # (archive_path, data)
        total_size = 0

        for entry in entries:
            prefix = entry.hash[:2]
            content_path = self.store_dir / prefix / entry.hash
            manifest_path = self.store_dir / prefix / f"{entry.hash}.manifest.yaml"

            content = content_path.read_bytes() if content_path.exists() else b""
            manifest_data = manifest_path.read_bytes() if manifest_path.exists() else b""

            skill_dir = f"skills/{entry.name}@{entry.version}"
            file_entries.append((f"{skill_dir}/SKILL.md", content))
            file_entries.append((f"{skill_dir}/skill.yaml", manifest_data))

            index_records.append(
                {
                    "name": entry.name,
                    "version": entry.version,
                    "hash": entry.hash,
                }
            )
            total_size += len(content) + len(manifest_data)

        index_json = json.dumps(index_records, indent=2).encode()
        total_size += len(index_json)

        # Write archive
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if format == "tar.gz":
            with tarfile.open(str(output_path), "w:gz") as tar:
                self._add_bytes_to_tar(tar, "index.json", index_json)
                for archive_path, data in file_entries:
                    self._add_bytes_to_tar(tar, archive_path, data)
        else:
            with zipfile.ZipFile(str(output_path), "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("index.json", index_json)
                for archive_path, data in file_entries:
                    zf.writestr(archive_path, data)

        return {
            "path": str(output_path),
            "format": format,
            "skill_count": len(entries),
            "total_size": total_size,
        }

    @staticmethod
    def _add_bytes_to_tar(tar: tarfile.TarFile, name: str, data: bytes) -> None:
        """Add in-memory bytes to a tar archive."""
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        tar.addfile(info, BytesIO(data))

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
            os.replace(tmp_path, str(self.index_path))
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

    def _find_entry(self, index: list[IndexEntry], name: str, version: str) -> IndexEntry | None:
        for entry in index:
            if entry.name == name and entry.version == version:
                return entry
        return None

    def verify_consistency(self) -> dict:
        """Check store consistency: dangling index refs and orphaned blobs.

        Returns a dict with:
          - dangling_refs: list of index entries whose blob file is missing
          - orphaned_blobs: list of blob files not referenced by any index entry
          - ok: True if no issues found
        """
        index = self._load_index()

        # Collect all hashes referenced by the index
        indexed_hashes: set[str] = set()
        dangling_refs: list[str] = []
        for entry in index:
            indexed_hashes.add(entry.hash)
            prefix = entry.hash[:2]
            blob_path = self.store_dir / prefix / entry.hash
            if not blob_path.exists():
                dangling_refs.append(f"{entry.name}@{entry.version} (hash={entry.hash})")

        # Scan store_dir for blob files not in the index
        orphaned_blobs: list[str] = []
        if self.store_dir.is_dir():
            for prefix_dir in self.store_dir.iterdir():
                if not prefix_dir.is_dir():
                    continue
                for blob_file in prefix_dir.iterdir():
                    # Skip manifest files — only check content blobs
                    if blob_file.name.endswith(".manifest.yaml"):
                        continue
                    if blob_file.name not in indexed_hashes:
                        orphaned_blobs.append(str(blob_file))

        return {
            "dangling_refs": dangling_refs,
            "orphaned_blobs": orphaned_blobs,
            "ok": len(dangling_refs) == 0 and len(orphaned_blobs) == 0,
        }

    def _write_manifest(self, path: Path, manifest: SkillManifest):
        """Write manifest YAML alongside stored content."""
        with open(path, "w") as f:
            yaml.safe_dump(manifest.to_dict(), f)
