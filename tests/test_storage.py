"""Unit tests for FilesystemBackend — Task 2.3."""

from __future__ import annotations

import hashlib

import pytest

from skillctl.registry.storage import (
    FilesystemBackend,
    IntegrityError,
    NotFoundError,
)


@pytest.fixture
def backend(tmp_path):
    """Create a FilesystemBackend rooted in a temporary directory."""
    return FilesystemBackend(data_dir=tmp_path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- store and retrieve -----------------------------------------------------


@pytest.mark.anyio
async def test_store_and_retrieve(backend: FilesystemBackend):
    content = b"hello world"
    h = await backend.store_blob(content)
    assert h == _sha256(content)

    retrieved = await backend.get_blob(h)
    assert retrieved == content


# -- delete blob ------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_blob(backend: FilesystemBackend):
    content = b"to be deleted"
    h = await backend.store_blob(content)

    await backend.delete_blob(h)
    assert not await backend.exists(h)


@pytest.mark.anyio
async def test_delete_missing_blob_raises(backend: FilesystemBackend):
    with pytest.raises(NotFoundError):
        await backend.delete_blob("ab" * 32)


# -- idempotent store -------------------------------------------------------


@pytest.mark.anyio
async def test_idempotent_store(backend: FilesystemBackend):
    content = b"same content twice"
    h1 = await backend.store_blob(content)
    h2 = await backend.store_blob(content)
    assert h1 == h2

    retrieved = await backend.get_blob(h1)
    assert retrieved == content


# -- integrity check --------------------------------------------------------


@pytest.mark.anyio
async def test_integrity_check_on_corrupted_blob(backend: FilesystemBackend):
    content = b"original content"
    h = await backend.store_blob(content)

    # Corrupt the stored file
    blob_path = backend._blob_path(h)
    blob_path.write_bytes(b"corrupted!")

    with pytest.raises(IntegrityError):
        await backend.get_blob(h)


@pytest.mark.anyio
async def test_storing_again_repairs_corrupted_blob(backend: FilesystemBackend):
    content = b"repairable content"
    digest = await backend.store_blob(content)
    backend._blob_path(digest).write_bytes(b"corrupted")

    repaired_digest = await backend.store_blob(content)

    assert repaired_digest == digest
    assert await backend.get_blob(digest) == content


# -- missing blob error -----------------------------------------------------


@pytest.mark.anyio
async def test_get_missing_blob_raises(backend: FilesystemBackend):
    with pytest.raises(NotFoundError):
        await backend.get_blob("ab" * 32)


# -- exists -----------------------------------------------------------------


@pytest.mark.anyio
async def test_exists_returns_true_after_store(backend: FilesystemBackend):
    content = b"check existence"
    h = await backend.store_blob(content)
    assert await backend.exists(h) is True


@pytest.mark.anyio
async def test_exists_returns_false_for_missing(backend: FilesystemBackend):
    assert await backend.exists("ab" * 32) is False


@pytest.mark.anyio
async def test_consistency_inventory_reports_recovery_conditions(
    backend: FilesystemBackend,
):
    corrupted = await backend.store_blob(b"referenced but corrupted")
    orphaned = await backend.store_blob(b"not referenced")
    missing = "cd" * 32
    backend._blob_path(corrupted).write_bytes(b"tampered")
    malformed = backend._blobs_dir / "bad" / "unexpected-name"
    malformed.parent.mkdir(parents=True)
    malformed.write_bytes(b"unknown")

    report = backend.check_consistency({corrupted, missing})

    assert report.status == "degraded"
    assert report.missing == (missing,)
    assert report.corrupted == (corrupted,)
    assert report.orphaned == (orphaned,)
    assert report.malformed == ("bad/unexpected-name",)


@pytest.mark.anyio
async def test_consistency_inventory_is_clean_for_referenced_blob(
    backend: FilesystemBackend,
):
    digest = await backend.store_blob(b"referenced")

    report = backend.check_consistency({digest})

    assert report.status == "ok"
    assert report.missing == ()
    assert report.corrupted == ()
    assert report.orphaned == ()
    assert report.malformed == ()
