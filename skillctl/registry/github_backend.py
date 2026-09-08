"""GitHub-backed storage — skills stored as files in a git repository.

Repo layout::

    skills/
      <namespace>/
        <name>/
          <version>/
            skill.yaml       # manifest JSON
            content           # skill content (single file or archive)
            artifact.zip      # complete immutable artifact bundle
            metadata.json     # eval scores, timestamps

The backend maintains a local clone for fast reads and pushes to GitHub
on writes.  A SQLite index is rebuilt from the repo on startup for FTS search.

Security notes
--------------
- The PAT is **never** embedded in the clone URL.  We use ``GIT_ASKPASS``
  with a one-shot helper script so the token never lands in argv, error
  output, or the local credential cache.
- Every name/version received from the registry layer is re-validated
  against a strict regex before any filesystem or git operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from skillctl.errors import SkillctlError
from skillctl.registry.db import MetadataDB, SkillRecord
from skillctl.registry.storage import StorageBackend, NotFoundError


# Strict patterns matching the registry's published validator constraints.
_NAMESPACE_NAME_PATTERN = re.compile(r"^[a-z0-9-]+/[a-z0-9-]+$")
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def _validate_name_version(name: str, version: str) -> None:
    if not _NAMESPACE_NAME_PATTERN.match(name):
        raise SkillctlError(
            code="E_INVALID_NAME",
            what=f"Invalid skill name: {name!r}",
            why="Must match <namespace>/<name> with characters [a-z0-9-]",
            fix="Rename the skill to use lowercase letters, numbers, and hyphens",
        )
    if not _VERSION_PATTERN.match(version):
        raise SkillctlError(
            code="E_INVALID_VERSION",
            what=f"Invalid skill version: {version!r}",
            why="Must be a valid semver like 1.2.3 or 1.2.3-rc.1",
            fix="Bump to a valid semver version",
        )


class GitHubBackend(StorageBackend):
    """Git-backed storage that keeps skills in a GitHub repository.

    Parameters
    ----------
    repo_url : str
        HTTPS clone URL, e.g. ``https://github.com/org/skill-registry.git``.
    clone_dir : Path
        Local directory for the clone.
    branch : str
        Branch to use (default ``main``).
    github_token : str | None
        Personal access token — supplied to git via ``GIT_ASKPASS`` so it
        never lands in argv or error output.
    """

    def __init__(
        self,
        repo_url: str,
        clone_dir: Path,
        branch: str = "main",
        github_token: str | None = None,
    ) -> None:
        self._repo_url = repo_url
        self._clone_dir = clone_dir
        self._branch = branch
        self._token = github_token
        self._skills_dir = clone_dir / "skills"

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Clone the repo (or pull if it already exists)."""
        if (self._clone_dir / ".git").is_dir():
            self._git("fetch", "origin", self._branch)
            self._git("reset", "--hard", f"origin/{self._branch}")
        else:
            self._clone_dir.mkdir(parents=True, exist_ok=True)
            with self._git_env() as env:
                subprocess.run(
                    [
                        "git",
                        "clone",
                        "--branch",
                        self._branch,
                        "--single-branch",
                        self._repo_url,
                        str(self._clone_dir),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                )
        self._ensure_commit_identity()
        self._skills_dir.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Skill-aware storage (used by the registry layer)
    # ------------------------------------------------------------------

    def store_skill(
        self,
        name: str,
        version: str,
        manifest_json: str,
        content: bytes,
        metadata: dict,
        artifact: bytes | None = None,
    ) -> str:
        """Write skill files to the repo, commit, and push.

        Returns the SHA-256 hash of the content.
        """
        _validate_name_version(name, version)
        namespace, skill_name = name.split("/", 1)
        skill_dir = self._skills_dir / namespace / skill_name / version
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Write files
        (skill_dir / "skill.yaml").write_text(manifest_json)
        (skill_dir / "content").write_bytes(content)
        if artifact is not None:
            (skill_dir / "artifact.zip").write_bytes(artifact)
        (skill_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

        content_hash = hashlib.sha256(content).hexdigest()

        # Git add + commit + push
        self._git("add", "-A")
        self._git(
            "commit",
            "-m",
            f"publish: {name}@{version}",
            "--allow-empty",
        )
        self._push()

        return content_hash

    def delete_skill(self, name: str, version: str) -> None:
        """Remove a skill version from the repo, commit, and push."""
        _validate_name_version(name, version)
        namespace, skill_name = name.split("/", 1)
        skill_dir = self._skills_dir / namespace / skill_name / version

        if not skill_dir.is_dir():
            raise NotFoundError(f"{name}@{version}")

        shutil.rmtree(skill_dir)

        # Clean up empty parent dirs
        name_dir = skill_dir.parent
        if name_dir.is_dir() and not any(name_dir.iterdir()):
            name_dir.rmdir()
            ns_dir = name_dir.parent
            if ns_dir.is_dir() and not any(ns_dir.iterdir()):
                ns_dir.rmdir()

        self._git("add", "-A")
        self._git("commit", "-m", f"delete: {name}@{version}", "--allow-empty")
        self._push()

    def get_skill_content(self, name: str, version: str) -> bytes:
        """Read skill content bytes from the local clone."""
        _validate_name_version(name, version)
        namespace, skill_name = name.split("/", 1)
        content_path = self._skills_dir / namespace / skill_name / version / "content"
        if not content_path.is_file():
            raise NotFoundError(f"{name}@{version}")
        return content_path.read_bytes()

    def get_skill_artifact(self, name: str, version: str) -> bytes:
        """Read the complete artifact, synthesizing one for legacy records."""
        from skillctl.artifact import build_minimal_artifact
        from skillctl.manifest import ManifestLoader

        _validate_name_version(name, version)
        namespace, skill_name = name.split("/", 1)
        skill_dir = self._skills_dir / namespace / skill_name / version
        artifact_path = skill_dir / "artifact.zip"
        if artifact_path.is_file():
            return artifact_path.read_bytes()

        manifest_path = skill_dir / "skill.yaml"
        content_path = skill_dir / "content"
        if not manifest_path.is_file() or not content_path.is_file():
            raise NotFoundError(f"{name}@{version}")
        try:
            raw = json.loads(manifest_path.read_text())
            manifest = ManifestLoader()._dict_to_manifest(raw)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SkillctlError(
                code="E_INVALID_ARTIFACT",
                what=f"Cannot synthesize legacy artifact for {name}@{version}",
                why=str(exc),
                fix="Re-publish this skill version with a complete artifact",
            ) from exc
        return build_minimal_artifact(manifest, content_path.read_bytes())

    def update_metadata(self, name: str, version: str, metadata: dict) -> None:
        """Merge fields into metadata.json for a skill version, commit, and push."""
        _validate_name_version(name, version)
        namespace, skill_name = name.split("/", 1)
        meta_path = self._skills_dir / namespace / skill_name / version / "metadata.json"
        if not meta_path.parent.is_dir():
            raise NotFoundError(f"{name}@{version}")

        existing: dict = {}
        if meta_path.is_file():
            try:
                existing = json.loads(meta_path.read_text())
            except json.JSONDecodeError:
                existing = {}
        existing.update(metadata)
        meta_path.write_text(json.dumps(existing, indent=2))
        self._git("add", "-A")
        self._git("commit", "-m", f"update-meta: {name}@{version}", "--allow-empty")
        self._push()

    def pull(self) -> None:
        """Pull latest changes from remote."""
        self._git("pull", "--rebase", "origin", self._branch)

    # ------------------------------------------------------------------
    # StorageBackend interface (content-addressed, for compatibility)
    # ------------------------------------------------------------------

    async def store_blob(self, content: bytes) -> str:
        """Store content in a flat blobs area (fallback for non-skill data)."""
        content_hash = hashlib.sha256(content).hexdigest()
        blob_dir = self._clone_dir / "blobs" / content_hash[:2]
        blob_dir.mkdir(parents=True, exist_ok=True)
        blob_path = blob_dir / content_hash
        if not blob_path.exists():
            blob_path.write_bytes(content)
        return content_hash

    async def get_blob(self, content_hash: str) -> bytes:
        """Retrieve a blob by hash — checks both skill content and flat blobs."""
        # First check flat blobs
        blob_path = self._clone_dir / "blobs" / content_hash[:2] / content_hash
        if blob_path.is_file():
            return blob_path.read_bytes()

        # Search skill content files by hash
        if self._skills_dir.is_dir():
            for content_file in self._iter_blob_files():
                data = content_file.read_bytes()
                if hashlib.sha256(data).hexdigest() == content_hash:
                    return data

        raise NotFoundError(content_hash)

    async def exists(self, content_hash: str) -> bool:
        blob_path = self._clone_dir / "blobs" / content_hash[:2] / content_hash
        if blob_path.is_file():
            return True
        # Check skill content files
        if self._skills_dir.is_dir():
            for content_file in self._iter_blob_files():
                data = content_file.read_bytes()
                if hashlib.sha256(data).hexdigest() == content_hash:
                    return True
        return False

    async def delete_blob(self, content_hash: str) -> None:
        blob_path = self._clone_dir / "blobs" / content_hash[:2] / content_hash
        if blob_path.is_file():
            blob_path.unlink()
            return
        raise NotFoundError(content_hash)

    def _iter_blob_files(self):
        """Yield content and artifact files stored in skill version directories."""
        yield from self._skills_dir.rglob("content")
        yield from self._skills_dir.rglob("artifact.zip")

    # ------------------------------------------------------------------
    # Index rebuild — scan repo and populate SQLite for FTS search
    # ------------------------------------------------------------------

    def rebuild_index(self, db: MetadataDB) -> int:
        """Scan the repo and insert/update all skills into the SQLite index.

        Returns the number of skills indexed.
        """
        count = 0
        if not self._skills_dir.is_dir():
            return 0

        for ns_dir in sorted(self._skills_dir.iterdir()):
            if not ns_dir.is_dir():
                continue
            namespace = ns_dir.name
            for name_dir in sorted(ns_dir.iterdir()):
                if not name_dir.is_dir():
                    continue
                skill_name = name_dir.name
                full_name = f"{namespace}/{skill_name}"
                # Skip directories whose names don't match the strict pattern.
                if not _NAMESPACE_NAME_PATTERN.match(full_name):
                    continue
                for ver_dir in sorted(name_dir.iterdir()):
                    if not ver_dir.is_dir():
                        continue
                    version = ver_dir.name
                    if not _VERSION_PATTERN.match(version):
                        continue
                    # Skip if already indexed
                    if db.get_skill(full_name, version) is not None:
                        count += 1
                        continue
                    record = self._read_skill_record(full_name, namespace, version, ver_dir)
                    if record:
                        try:
                            db.insert_skill(record)
                            count += 1
                        except Exception:
                            # Duplicate or other error — skip
                            count += 1
        return count

    def _read_skill_record(self, full_name: str, namespace: str, version: str, ver_dir: Path) -> SkillRecord | None:
        """Read a SkillRecord from a version directory in the repo.

        Defensive: re-validate name/version even though :meth:`rebuild_index`
        has already filtered them.  Cheap insurance against future callers.
        """
        if not _NAMESPACE_NAME_PATTERN.match(full_name):
            return None
        if not _VERSION_PATTERN.match(version):
            return None

        manifest_path = ver_dir / "skill.yaml"
        content_path = ver_dir / "content"
        meta_path = ver_dir / "metadata.json"

        if not content_path.is_file():
            return None

        # Read manifest
        manifest_json = "{}"
        manifest_dict: dict = {}
        if manifest_path.is_file():
            manifest_json = manifest_path.read_text()
            try:
                manifest_dict = json.loads(manifest_json)
            except json.JSONDecodeError:
                pass

        # Read metadata
        metadata: dict = {}
        if meta_path.is_file():
            try:
                metadata = json.loads(meta_path.read_text())
            except json.JSONDecodeError:
                pass

        # Compute content hash
        content_bytes = content_path.read_bytes()
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        artifact_path = ver_dir / "artifact.zip"
        bundle_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest() if artifact_path.is_file() else None

        # Extract fields from manifest
        meta_section = manifest_dict.get("metadata", {})

        return SkillRecord(
            id=None,
            name=full_name,
            namespace=metadata.get("rbac_namespace", namespace),
            version=version,
            description=meta_section.get("description", ""),
            content_hash=content_hash,
            artifact_hash=bundle_hash,
            tags=meta_section.get("tags", []),
            authors=meta_section.get("authors", []),
            license=meta_section.get("license"),
            eval_grade=metadata.get("eval_grade"),
            eval_score=metadata.get("eval_score"),
            status=metadata.get("status", "published"),
            created_at=metadata.get("created_at", ""),
            updated_at=metadata.get("updated_at", ""),
            manifest_json=manifest_json,
        )

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    def _ensure_commit_identity(self) -> None:
        """Configure a repository-local fallback identity when none exists."""
        defaults = {
            "user.name": "SkillsOps Registry",
            "user.email": "skillctl@localhost",
        }
        for key, value in defaults.items():
            try:
                configured = self._git("config", "--get", key).stdout.strip()
            except subprocess.CalledProcessError:
                configured = ""
            if not configured:
                self._git("config", "--local", key, value)

    def _git_env(self):
        """Yield an env dict configured for non-interactive token auth.

        Uses ``GIT_ASKPASS`` with a temp helper script that prints the PAT
        on stdout when git asks for a password.  The script is created in a
        per-call temp directory and cleaned up on exit, so the token never
        lives on disk longer than the git invocation.
        """
        # Defer the contextmanager import so the module stays importable
        # without contextlib.contextmanager at top-level.
        from contextlib import contextmanager

        @contextmanager
        def _env_ctx():
            env = os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"
            env["GIT_CONFIG_NOSYSTEM"] = "1"
            tmp: tempfile.TemporaryDirectory | None = None
            if self._token:
                tmp = tempfile.TemporaryDirectory(prefix="skillctl-askpass-")
                helper_path = Path(tmp.name) / "askpass.sh"
                helper_path.write_text(
                    "#!/usr/bin/env sh\n"
                    'case "$1" in\n'
                    "    Username*) echo 'x-access-token' ;;\n"
                    "    *) printf '%s' \"$GIT_TOKEN\" ;;\n"
                    "esac\n"
                )
                helper_path.chmod(stat.S_IRWXU)  # 0o700
                env["GIT_ASKPASS"] = str(helper_path)
                env["GIT_TOKEN"] = self._token
            try:
                yield env
            finally:
                if tmp is not None:
                    tmp.cleanup()

        return _env_ctx()

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        """Run a git command in the clone directory."""
        with self._git_env() as env:
            try:
                return subprocess.run(
                    ["git", *args],
                    cwd=str(self._clone_dir),
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                )
            except subprocess.CalledProcessError as e:
                sanitized_cmd = e.cmd
                sanitized_out = e.output or ""
                sanitized_err = e.stderr or ""
                if self._token:
                    sanitized_cmd = [a.replace(self._token, "***") for a in e.cmd]
                    sanitized_out = sanitized_out.replace(self._token, "***")
                    sanitized_err = sanitized_err.replace(self._token, "***")
                raise subprocess.CalledProcessError(
                    e.returncode,
                    sanitized_cmd,
                    sanitized_out,
                    sanitized_err,
                ) from None

    def _push(self) -> None:
        """Push to remote (auth comes from GIT_ASKPASS env)."""
        self._git("push", "origin", self._branch)
