"""Storage backend — content-addressed blob storage.

Defines the abstract ``StorageBackend`` interface and the default
``FilesystemBackend`` implementation that stores blobs under
``<data_dir>/blobs/<hash[:2]>/<hash>`` using atomic writes.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from skillctl.errors import SkillctlError


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class NotFoundError(SkillctlError):
    """Raised when a requested blob does not exist in storage."""

    def __init__(self, content_hash: str) -> None:
        super().__init__(
            code="E_BLOB_NOT_FOUND",
            what=f"Blob {content_hash} not found in storage",
            why="The requested content hash does not correspond to any stored blob.",
            fix="Verify the content hash is correct, or re-publish the skill.",
        )


class IntegrityError(SkillctlError):
    """Raised when a stored blob fails integrity verification."""

    def __init__(self, content_hash: str, actual_hash: str) -> None:
        super().__init__(
            code="E_INTEGRITY",
            what=f"Integrity check failed for blob {content_hash}",
            why=f"Expected hash {content_hash} but got {actual_hash}.",
            fix="The stored blob is corrupted. Re-publish the skill to restore it.",
        )


# ---------------------------------------------------------------------------
# Abstract base class (Task 2.1)
# ---------------------------------------------------------------------------


class StorageBackend(ABC):
    """Abstract content-addressed blob storage."""

    @abstractmethod
    async def store_blob(self, content: bytes) -> str:
        """Store *content* and return its SHA-256 hex digest."""
        ...

    @abstractmethod
    async def get_blob(self, content_hash: str) -> bytes:
        """Retrieve blob by *content_hash*.  Raises ``NotFoundError``."""
        ...

    @abstractmethod
    async def exists(self, content_hash: str) -> bool:
        """Return ``True`` if a blob with *content_hash* is stored."""
        ...

    @abstractmethod
    async def delete_blob(self, content_hash: str) -> None:
        """Delete the blob identified by *content_hash*.  Raises ``NotFoundError``."""
        ...


# ---------------------------------------------------------------------------
# Filesystem implementation (Task 2.2)
# ---------------------------------------------------------------------------


class FilesystemBackend(StorageBackend):
    """Content-addressed filesystem storage under ``data_dir/blobs/<prefix>/<hash>``."""

    def __init__(self, data_dir: Path) -> None:
        self._blobs_dir = data_dir / "blobs"
        self._blobs_dir.mkdir(parents=True, exist_ok=True)

    # -- helpers -------------------------------------------------------------

    _HASH_RE = __import__("re").compile(r"[0-9a-f]{64}\Z")

    def _blob_path(self, content_hash: str) -> Path:
        """Return ``blobs/<first-two-chars>/<full-hash>``."""
        if not self._HASH_RE.match(content_hash):
            raise ValueError(f"Invalid content hash: {content_hash!r}")
        return self._blobs_dir / content_hash[:2] / content_hash

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    # -- public API ----------------------------------------------------------

    async def store_blob(self, content: bytes) -> str:
        content_hash = self._sha256(content)
        dest = self._blob_path(content_hash)

        if dest.exists():
            # Idempotent — same content already stored.
            return content_hash

        dest.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to a temp file in the same directory, then rename.
        fd, tmp_path = tempfile.mkstemp(dir=dest.parent)
        try:
            os.write(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(tmp_path, dest)
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        return content_hash

    async def get_blob(self, content_hash: str) -> bytes:
        dest = self._blob_path(content_hash)

        if not dest.exists():
            raise NotFoundError(content_hash)

        data = dest.read_bytes()

        # Integrity verification
        actual = self._sha256(data)
        if actual != content_hash:
            raise IntegrityError(content_hash, actual)

        return data

    async def exists(self, content_hash: str) -> bool:
        return self._blob_path(content_hash).exists()

    async def delete_blob(self, content_hash: str) -> None:
        dest = self._blob_path(content_hash)

        if not dest.exists():
            raise NotFoundError(content_hash)

        dest.unlink()
