"""GitHub-backed storage — skills stored as files in a git repository.

Repo layout::

    skills/
      <namespace>/
        <name>/
          <version>/
            skill.yaml       # manifest JSON
            content           # skill content (single file or archive)
            metadata.json     # eval scores, timestamps

The backend maintains a local clone for fast reads and pushes to GitHub
on writes.  A SQLite index is rebuilt from the repo on startup for FTS search.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path

from skillctl.registry.db import MetadataDB, SkillRecord
from skillctl.registry.storage import StorageBackend, NotFoundError


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
        Personal access token — injected into the clone URL for push access.
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

        # Build the authenticated URL for push
        self._auth_url = self._build_auth_url(repo_url, github_token)

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
            subprocess.run(
                ["git", "clone", "--branch", self._branch, "--single-branch",
                 self._auth_url, str(self._clone_dir)],
                check=True, capture_output=True, text=True,
            )
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
    ) -> str:
        """Write skill files to the repo, commit, and push.

        Returns the SHA-256 hash of the content.
        """
        namespace, skill_name = name.split("/", 1)
        skill_dir = self._skills_dir / namespace / skill_name / version
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Write files
        (skill_dir / "skill.yaml").write_text(manifest_json)
        (skill_dir / "content").write_bytes(content)
        (skill_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2)
        )

        content_hash = hashlib.sha256(content).hexdigest()

        # Git add + commit + push
        self._git("add", "-A")
        self._git(
            "commit", "-m",
            f"publish: {name}@{version}",
            "--allow-empty",
        )
        self._push()

        return content_hash

    def delete_skill(self, name: str, version: str) -> None:
        """Remove a skill version from the repo, commit, and push."""
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
        namespace, skill_name = name.split("/", 1)
        content_path = self._skills_dir / namespace / skill_name / version / "content"
        if not content_path.is_file():
            raise NotFoundError(f"{name}@{version}")
        return content_path.read_bytes()

    def update_metadata(self, name: str, version: str, metadata: dict) -> None:
        """Update metadata.json for a skill version, commit, and push."""
        namespace, skill_name = name.split("/", 1)
        meta_path = self._skills_dir / namespace / skill_name / version / "metadata.json"
        if not meta_path.parent.is_dir():
            raise NotFoundError(f"{name}@{version}")

        meta_path.write_text(json.dumps(metadata, indent=2))
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
            for content_file in self._skills_dir.rglob("content"):
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
            for content_file in self._skills_dir.rglob("content"):
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
                for ver_dir in sorted(name_dir.iterdir()):
                    if not ver_dir.is_dir():
                        continue
                    version = ver_dir.name
                    # Skip if already indexed
                    if db.get_skill(full_name, version) is not None:
                        count += 1
                        continue
                    record = self._read_skill_record(
                        full_name, namespace, version, ver_dir
                    )
                    if record:
                        try:
                            db.insert_skill(record)
                            count += 1
                        except Exception:
                            # Duplicate or other error — skip
                            count += 1
        return count

    def _read_skill_record(
        self, full_name: str, namespace: str, version: str, ver_dir: Path
    ) -> SkillRecord | None:
        """Read a SkillRecord from a version directory in the repo."""
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

        # Extract fields from manifest
        meta_section = manifest_dict.get("metadata", {})

        return SkillRecord(
            id=None,
            name=full_name,
            namespace=namespace,
            version=version,
            description=meta_section.get("description", ""),
            content_hash=content_hash,
            tags=meta_section.get("tags", []),
            authors=meta_section.get("authors", []),
            license=meta_section.get("license"),
            eval_grade=metadata.get("eval_grade"),
            eval_score=metadata.get("eval_score"),
            created_at=metadata.get("created_at", ""),
            updated_at=metadata.get("updated_at", ""),
            manifest_json=manifest_json,
        )

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        """Run a git command in the clone directory."""
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        return subprocess.run(
            ["git", *args],
            cwd=str(self._clone_dir),
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    def _push(self) -> None:
        """Push to remote, setting the upstream URL with token."""
        self._git("push", self._auth_url, self._branch)

    @staticmethod
    def _build_auth_url(repo_url: str, token: str | None) -> str:
        """Inject token into HTTPS URL for authenticated push."""
        if not token:
            return repo_url
        # https://github.com/... → https://<token>@github.com/...
        if repo_url.startswith("https://"):
            return repo_url.replace("https://", f"https://{token}@", 1)
        return repo_url
