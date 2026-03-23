"""Unit tests for GitHubBackend — local git operations (no remote push)."""

from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from skillctl.registry.db import MetadataDB
from skillctl.registry.github_backend import GitHubBackend
from skillctl.registry.storage import NotFoundError


@pytest.fixture
def git_repo(tmp_path):
    """Create a bare git repo and a GitHubBackend clone of it."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

    clone_dir = tmp_path / "clone"
    backend = GitHubBackend(
        repo_url=str(bare),
        clone_dir=clone_dir,
        branch="main",
        github_token=None,
    )

    # Initialize the clone with an initial commit so main branch exists
    clone_dir.mkdir()
    subprocess.run(["git", "clone", str(bare), str(clone_dir)], check=True, capture_output=True)
    (clone_dir / "README.md").write_text("# Skill Registry\n")
    subprocess.run(["git", "add", "-A"], cwd=str(clone_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(clone_dir), check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=str(clone_dir), check=True, capture_output=True)

    # Now set up properly via setup()
    backend.setup()
    return backend


@pytest.fixture
def db(tmp_path):
    mdb = MetadataDB(tmp_path / "test.db")
    mdb.initialize()
    yield mdb
    mdb.close()


CONTENT = b"# Hello Skill\nDo the thing."
MANIFEST = json.dumps({
    "apiVersion": "skillctl.io/v1",
    "kind": "Skill",
    "metadata": {
        "name": "my-org/hello",
        "version": "1.0.0",
        "description": "A test skill",
        "tags": ["test"],
        "authors": [{"name": "Alice"}],
    },
    "spec": {"content": {"inline": "uploaded"}},
}, indent=2)


def test_store_and_read_skill(git_repo: GitHubBackend):
    content_hash = git_repo.store_skill(
        "my-org/hello", "1.0.0", MANIFEST, CONTENT,
        {"created_at": "2025-01-01T00:00:00Z"},
    )
    assert content_hash == hashlib.sha256(CONTENT).hexdigest()

    retrieved = git_repo.get_skill_content("my-org/hello", "1.0.0")
    assert retrieved == CONTENT


def test_store_creates_git_commit(git_repo: GitHubBackend):
    git_repo.store_skill("my-org/hello", "1.0.0", MANIFEST, CONTENT, {})

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=str(git_repo._clone_dir), capture_output=True, text=True,
    )
    assert "publish: my-org/hello@1.0.0" in log.stdout


def test_delete_skill(git_repo: GitHubBackend):
    git_repo.store_skill("my-org/hello", "1.0.0", MANIFEST, CONTENT, {})
    git_repo.delete_skill("my-org/hello", "1.0.0")

    with pytest.raises(NotFoundError):
        git_repo.get_skill_content("my-org/hello", "1.0.0")


def test_delete_nonexistent_raises(git_repo: GitHubBackend):
    with pytest.raises(NotFoundError):
        git_repo.delete_skill("no-org/nothing", "0.0.0")


def test_rebuild_index(git_repo: GitHubBackend, db: MetadataDB):
    git_repo.store_skill("my-org/hello", "1.0.0", MANIFEST, CONTENT,
                         {"created_at": "2025-01-01", "eval_grade": "A", "eval_score": 95.0})
    git_repo.store_skill("my-org/hello", "1.1.0", MANIFEST, CONTENT,
                         {"created_at": "2025-02-01"})

    count = git_repo.rebuild_index(db)
    assert count == 2

    record = db.get_skill("my-org/hello", "1.0.0")
    assert record is not None
    assert record.eval_grade == "A"
    assert record.eval_score == 95.0

    versions = db.get_versions("my-org/hello")
    assert len(versions) == 2


def test_rebuild_index_idempotent(git_repo: GitHubBackend, db: MetadataDB):
    git_repo.store_skill("my-org/hello", "1.0.0", MANIFEST, CONTENT, {})
    git_repo.rebuild_index(db)
    count = git_repo.rebuild_index(db)
    assert count == 1  # Already indexed, not duplicated


def test_update_metadata(git_repo: GitHubBackend):
    git_repo.store_skill("my-org/hello", "1.0.0", MANIFEST, CONTENT, {"eval_grade": None})
    git_repo.update_metadata("my-org/hello", "1.0.0", {"eval_grade": "B", "eval_score": 80.0})

    meta_path = git_repo._skills_dir / "my-org" / "hello" / "1.0.0" / "metadata.json"
    meta = json.loads(meta_path.read_text())
    assert meta["eval_grade"] == "B"
    assert meta["eval_score"] == 80.0


@pytest.mark.anyio
async def test_blob_interface_compatibility(git_repo: GitHubBackend):
    """The StorageBackend interface still works for backward compat."""
    h = await git_repo.store_blob(b"test data")
    assert len(h) == 64

    data = await git_repo.get_blob(h)
    assert data == b"test data"

    assert await git_repo.exists(h) is True
    await git_repo.delete_blob(h)
    assert await git_repo.exists(h) is False
