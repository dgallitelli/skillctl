"""Content-addressed storage for validated skills."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import NoReturn

import yaml

from skillctl.artifact import artifact_hash, build_minimal_artifact, inspect_artifact
from skillctl.errors import SkillctlError
from skillctl.manifest import ManifestLoader, SkillManifest

DEFAULT_STORE_ROOT = Path.home() / ".skillctl"
_MAX_IMPORT_FILES = 10_000
_MAX_IMPORT_BYTES = 1_000_000_000


@dataclass
class IndexEntry:
    name: str
    version: str
    hash: str
    tags: list[str]
    pushed_at: str  # ISO 8601
    size: int
    artifact_hash: str | None = None
    artifact_size: int = 0


@dataclass
class PushResult:
    hash: str
    path: str
    size: int
    created: bool  # True if new, False if already existed
    artifact_hash: str | None = None
    artifact_size: int = 0


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
        artifact: bytes | None = None,
    ) -> PushResult:
        """Store a validated skill. Returns PushResult."""
        content_hash = hashlib.sha256(content).hexdigest()
        artifact_bytes = artifact if artifact is not None else build_minimal_artifact(manifest, content)
        inspect_artifact(
            artifact_bytes,
            expected_name=manifest.metadata.name,
            expected_version=manifest.metadata.version,
            expected_content=content,
        )
        bundle_hash = artifact_hash(artifact_bytes)
        prefix = content_hash[:2]
        store_path = self.store_dir / prefix / content_hash
        artifact_path = self.store_dir / bundle_hash[:2] / bundle_hash

        if dry_run:
            return PushResult(
                hash=content_hash,
                path=str(store_path),
                size=len(content),
                created=not store_path.exists(),
                artifact_hash=bundle_hash,
                artifact_size=len(artifact_bytes),
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

        # Write content and complete artifact blobs atomically.
        self._write_blob(store_path, content)
        self._write_blob(artifact_path, artifact_bytes)

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
            artifact_hash=bundle_hash,
            artifact_size=len(artifact_bytes),
        )
        index.append(entry)
        self._save_index(index)

        return PushResult(
            hash=content_hash,
            path=str(store_path),
            size=len(content),
            created=True,
            artifact_hash=bundle_hash,
            artifact_size=len(artifact_bytes),
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

    def pull_artifact(self, name: str, version: str) -> tuple[bytes, dict]:
        """Retrieve and verify the complete artifact bundle for name@version."""
        index = self._load_index()
        entry = self._find_entry(index, name, version)
        if not entry:
            raise SkillctlError(
                code="E_NOT_FOUND",
                what=f"{name}@{version} not found in local store",
                why="The skill must be pushed before its artifact can be pulled",
                fix="Run 'skillctl push' first, or check 'skillctl list'",
            )

        content, metadata = self.pull(name, version)
        if entry.artifact_hash:
            bundle_path = self.store_dir / entry.artifact_hash[:2] / entry.artifact_hash
            try:
                bundle = bundle_path.read_bytes()
            except FileNotFoundError as exc:
                raise SkillctlError(
                    code="E_INTEGRITY",
                    what=f"Artifact blob is missing for {name}@{version}",
                    why=f"Expected artifact hash {entry.artifact_hash}",
                    fix="Re-push the skill to repair the store",
                ) from exc
            actual_hash = artifact_hash(bundle)
            if actual_hash != entry.artifact_hash:
                raise SkillctlError(
                    code="E_INTEGRITY",
                    what=f"Artifact hash mismatch for {name}@{version}",
                    why=f"Expected {entry.artifact_hash}, got {actual_hash}",
                    fix="Re-push the skill to repair the store",
                )
        else:
            manifest_path = self.store_dir / entry.hash[:2] / f"{entry.hash}.manifest.yaml"
            manifest, _ = ManifestLoader().load(str(manifest_path))
            bundle = build_minimal_artifact(manifest, content)

        inspect_artifact(
            bundle,
            expected_name=name,
            expected_version=version,
            expected_content=content,
        )
        return bundle, metadata

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

        # Then remove unreferenced files (orphaned blobs are harmless;
        # dangling index refs are not).
        content_still_referenced = any(item.hash == entry.hash for item in index)
        if not content_still_referenced:
            prefix = entry.hash[:2]
            content_path = self.store_dir / prefix / entry.hash
            manifest_path = self.store_dir / prefix / f"{entry.hash}.manifest.yaml"
            if content_path.exists():
                content_path.unlink()
            if manifest_path.exists():
                manifest_path.unlink()

        artifact_still_referenced = entry.artifact_hash and any(
            item.artifact_hash == entry.artifact_hash for item in index
        )
        if entry.artifact_hash and not artifact_still_referenced:
            artifact_path = self.store_dir / entry.artifact_hash[:2] / entry.artifact_hash
            if artifact_path.exists():
                artifact_path.unlink()

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
            artifact_data, _ = self.pull_artifact(entry.name, entry.version)

            skill_dir = f"skills/{entry.name}@{entry.version}"
            file_entries.append((f"{skill_dir}/SKILL.md", content))
            file_entries.append((f"{skill_dir}/skill.yaml", manifest_data))
            file_entries.append((f"{skill_dir}/artifact.zip", artifact_data))

            index_records.append(
                {
                    "name": entry.name,
                    "version": entry.version,
                    "hash": entry.hash,
                    "artifact_hash": artifact_hash(artifact_data),
                }
            )
            total_size += len(content) + len(manifest_data) + len(artifact_data)

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
            if entry.artifact_hash:
                indexed_hashes.add(entry.artifact_hash)
                artifact_path = self.store_dir / entry.artifact_hash[:2] / entry.artifact_hash
                if not artifact_path.exists():
                    dangling_refs.append(f"{entry.name}@{entry.version} (artifact_hash={entry.artifact_hash})")

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

    def import_skills(self, archive_path: Path) -> dict:
        """Import skills from a tar.gz or zip archive into the local store.

        Returns dict with: imported_count, skipped_count, errors
        """
        archive_path = Path(archive_path)
        name_lower = archive_path.name.lower()

        if name_lower.endswith(".tar.gz") or name_lower.endswith(".tgz"):
            fmt = "tar.gz"
        elif name_lower.endswith(".zip"):
            fmt = "zip"
        else:
            raise SkillctlError(
                code="E_INVALID_FORMAT",
                what=f"Unsupported archive format: {archive_path.name}",
                why="Only tar.gz and zip archives are supported for import",
                fix="Provide a .tar.gz or .zip archive created by 'skillctl export'",
            )

        imported_count = 0
        skipped_count = 0
        errors: list[str] = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)

            # Extract archive
            try:
                if fmt == "tar.gz":
                    with tarfile.open(str(archive_path), "r:gz") as tar:
                        members = tar.getmembers()
                        self._validate_archive_limits(
                            len(members),
                            sum(member.size for member in members if member.isfile()),
                        )
                        seen: set[str] = set()
                        for member in members:
                            destination = self._archive_destination(tmp, member.name, seen)
                            if member.isdir():
                                destination.mkdir(parents=True, exist_ok=True)
                            elif member.isfile():
                                source = tar.extractfile(member)
                                if source is None:
                                    self._invalid_archive(
                                        member.name,
                                        "Archive member could not be read",
                                    )
                                destination.parent.mkdir(parents=True, exist_ok=True)
                                with source, destination.open("wb") as output:
                                    shutil.copyfileobj(source, output)
                            else:
                                self._invalid_archive(
                                    member.name,
                                    "Links, devices, and other special archive members are not allowed",
                                )
                else:
                    with zipfile.ZipFile(str(archive_path), "r") as zf:
                        members = zf.infolist()
                        self._validate_archive_limits(
                            len(members),
                            sum(info.file_size for info in members),
                        )
                        seen = set()
                        for info in members:
                            destination = self._archive_destination(tmp, info.filename, seen)
                            file_type = (info.external_attr >> 16) & 0o170000
                            if file_type == stat.S_IFLNK:
                                self._invalid_archive(
                                    info.filename,
                                    "Symbolic links are not allowed",
                                )
                            if info.flag_bits & 0x1:
                                self._invalid_archive(
                                    info.filename,
                                    "Encrypted archive members are not supported",
                                )
                            if info.is_dir():
                                destination.mkdir(parents=True, exist_ok=True)
                            else:
                                destination.parent.mkdir(parents=True, exist_ok=True)
                                with zf.open(info, "r") as source, destination.open("wb") as output:
                                    shutil.copyfileobj(source, output)
            except (tarfile.TarError, zipfile.BadZipFile) as e:
                raise SkillctlError(
                    code="E_INVALID_ARCHIVE",
                    what=f"Failed to extract archive: {e}",
                    why="The archive file is corrupted or in an unexpected format",
                    fix="Re-export with 'skillctl export' and try again",
                ) from e

            # Read index.json
            index_path = tmp / "index.json"
            if not index_path.exists():
                raise SkillctlError(
                    code="E_INVALID_ARCHIVE",
                    what="Archive missing index.json",
                    why="A valid skillctl archive must contain an index.json file",
                    fix="Re-export with 'skillctl export' and try again",
                )

            try:
                index_data = json.loads(index_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                raise SkillctlError(
                    code="E_INVALID_ARCHIVE",
                    what=f"Failed to read index.json: {e}",
                    why="index.json must be valid JSON",
                    fix="Re-export with 'skillctl export' and try again",
                ) from e

            if not isinstance(index_data, list):
                raise SkillctlError(
                    code="E_INVALID_ARCHIVE",
                    what="Archive index.json must contain a list",
                    why="The archive index has an unexpected schema",
                    fix="Re-export with 'skillctl export' and try again",
                )

            loader = ManifestLoader()
            for entry in index_data:
                if not isinstance(entry, dict):
                    self._invalid_archive("index.json", "Every index entry must be an object")
                skill_name = entry.get("name", "")
                skill_version = entry.get("version", "")
                skill_dir = self._archive_destination(
                    tmp,
                    f"skills/{skill_name}@{skill_version}",
                )

                yaml_path = skill_dir / "skill.yaml"
                md_path = skill_dir / "SKILL.md"
                artifact_path = skill_dir / "artifact.zip"

                if not yaml_path.exists():
                    errors.append(f"{skill_name}@{skill_version}: skill.yaml not found in archive")
                    continue

                if not md_path.exists():
                    errors.append(f"{skill_name}@{skill_version}: SKILL.md not found in archive")
                    continue

                try:
                    manifest, _ = loader.load(str(skill_dir))
                    content = md_path.read_bytes()
                    artifact = artifact_path.read_bytes() if artifact_path.exists() else None

                    self.push(manifest, content, artifact=artifact)
                    imported_count += 1
                except SkillctlError as e:
                    if e.code == "E_ALREADY_EXISTS":
                        skipped_count += 1
                    else:
                        errors.append(f"{skill_name}@{skill_version}: {e.what}")
                except Exception as e:
                    errors.append(f"{skill_name}@{skill_version}: {e}")

        return {
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "errors": errors,
        }

    @staticmethod
    def _invalid_archive(name: str, why: str) -> NoReturn:
        raise SkillctlError(
            code="E_INVALID_ARCHIVE",
            what=f"Unsafe archive member: {name}",
            why=why,
            fix="Use an archive created by 'skillctl export'.",
        )

    @classmethod
    def _archive_destination(
        cls,
        root: Path,
        name: str,
        seen: set[str] | None = None,
    ) -> Path:
        """Resolve one POSIX archive member beneath *root* without following links."""
        if not isinstance(name, str) or "\\" in name:
            cls._invalid_archive(str(name), "Archive paths must use POSIX separators")
        relative = PurePosixPath(name)
        if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            cls._invalid_archive(name, "Absolute and traversal paths are not allowed")
        normalized = relative.as_posix()
        if seen is not None:
            if normalized in seen:
                cls._invalid_archive(name, "Duplicate archive paths are not allowed")
            seen.add(normalized)
        destination = root.joinpath(*relative.parts)
        try:
            destination.resolve().relative_to(root.resolve())
        except ValueError:
            cls._invalid_archive(name, "Archive path escapes the extraction directory")
        return destination

    @classmethod
    def _validate_archive_limits(cls, file_count: int, total_size: int) -> None:
        if file_count > _MAX_IMPORT_FILES or total_size > _MAX_IMPORT_BYTES:
            cls._invalid_archive(
                "archive",
                f"Archive exceeds limits ({file_count} members, {total_size} bytes)",
            )

    def _write_manifest(self, path: Path, manifest: SkillManifest):
        """Write manifest YAML alongside stored content."""
        with open(path, "w") as f:
            yaml.safe_dump(manifest.to_dict(), f)

    @staticmethod
    def _write_blob(path: Path, content: bytes) -> None:
        """Atomically write a content-addressed blob."""
        if path.exists() and path.read_bytes() == content:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent)
        try:
            remaining = memoryview(content)
            while remaining:
                written = os.write(tmp_fd, remaining)
                if written == 0:
                    raise OSError("Unable to make progress writing blob")
                remaining = remaining[written:]
            os.fsync(tmp_fd)
            os.close(tmp_fd)
            os.replace(tmp_path, str(path))
        except OSError as exc:
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
                why=str(exc),
                fix="Check disk space and permissions on ~/.skillctl/",
            ) from exc
