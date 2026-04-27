"""Unit tests for MetadataDB — Task 3.4."""

from __future__ import annotations

import pytest

from skillctl.registry.db import MetadataDB, SkillRecord


@pytest.fixture
def db(tmp_path):
    """Create an initialized MetadataDB backed by a temp file."""
    mdb = MetadataDB(tmp_path / "test.db")
    mdb.initialize()
    yield mdb
    mdb.close()


def _make_skill(
    name: str = "my-org/code-reviewer",
    namespace: str = "my-org",
    version: str = "1.0.0",
    description: str = "Reviews code",
    content_hash: str = "ab" * 32,
    tags: list[str] | None = None,
    authors: list[dict] | None = None,
    **kwargs,
) -> SkillRecord:
    return SkillRecord(
        id=None,
        name=name,
        namespace=namespace,
        version=version,
        description=description,
        content_hash=content_hash,
        tags=tags or ["code-review"],
        authors=authors or [{"name": "Alice", "email": "alice@example.com"}],
        manifest_json='{"name": "' + name + '"}',
        **kwargs,
    )


# -- initialize -------------------------------------------------------------


def test_initialize_creates_tables(db: MetadataDB):
    tables = {
        row[0]
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')").fetchall()
    }
    assert "skills" in tables
    assert "skills_fts" in tables
    assert "tokens" in tables
    assert "skills_ai" in tables
    assert "skills_ad" in tables
    assert "skills_au" in tables


def test_initialize_sets_wal_mode(db: MetadataDB):
    mode = db.conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert mode == "wal"


def test_initialize_is_idempotent(db: MetadataDB):
    # Calling initialize again should not raise
    db.initialize()


# -- insert and get ---------------------------------------------------------


def test_insert_and_get_skill(db: MetadataDB):
    skill = _make_skill()
    row_id = db.insert_skill(skill)
    assert row_id is not None and row_id > 0

    fetched = db.get_skill("my-org/code-reviewer", "1.0.0")
    assert fetched is not None
    assert fetched.name == "my-org/code-reviewer"
    assert fetched.namespace == "my-org"
    assert fetched.version == "1.0.0"
    assert fetched.description == "Reviews code"
    assert fetched.tags == ["code-review"]
    assert fetched.authors == [{"name": "Alice", "email": "alice@example.com"}]


def test_get_skill_returns_none_for_missing(db: MetadataDB):
    assert db.get_skill("no-org/nothing", "0.0.0") is None


def test_insert_duplicate_raises(db: MetadataDB):
    db.insert_skill(_make_skill())
    with pytest.raises(Exception):  # sqlite3.IntegrityError
        db.insert_skill(_make_skill())


# -- get_versions -----------------------------------------------------------


def test_get_versions(db: MetadataDB):
    db.insert_skill(_make_skill(version="1.0.0"))
    db.insert_skill(_make_skill(version="1.1.0"))
    db.insert_skill(_make_skill(version="2.0.0"))

    versions = db.get_versions("my-org/code-reviewer")
    assert len(versions) == 3
    # Ordered by created_at DESC — most recent first
    ver_strings = [v.version for v in versions]
    assert "1.0.0" in ver_strings
    assert "1.1.0" in ver_strings
    assert "2.0.0" in ver_strings


def test_get_versions_empty(db: MetadataDB):
    assert db.get_versions("no-org/nothing") == []


# -- delete_skill -----------------------------------------------------------


def test_delete_skill(db: MetadataDB):
    db.insert_skill(_make_skill())
    assert db.delete_skill("my-org/code-reviewer", "1.0.0") is True
    assert db.get_skill("my-org/code-reviewer", "1.0.0") is None


def test_delete_missing_skill_returns_false(db: MetadataDB):
    assert db.delete_skill("no-org/nothing", "0.0.0") is False


# -- update_eval ------------------------------------------------------------


def test_update_eval(db: MetadataDB):
    db.insert_skill(_make_skill())
    assert db.update_eval("my-org/code-reviewer", "1.0.0", "A", 95.5) is True

    fetched = db.get_skill("my-org/code-reviewer", "1.0.0")
    assert fetched is not None
    assert fetched.eval_grade == "A"
    assert fetched.eval_score == 95.5


def test_update_eval_missing_returns_false(db: MetadataDB):
    assert db.update_eval("no-org/nothing", "0.0.0", "B", 80.0) is False


# -- FTS5 search -------------------------------------------------------------


def test_search_by_query(db: MetadataDB):
    db.insert_skill(_make_skill(description="Automated code review tool"))
    db.insert_skill(
        _make_skill(
            name="my-org/linter",
            namespace="my-org",
            version="1.0.0",
            description="Linting utility",
            tags=["lint"],
        )
    )

    results = db.search(query="code review")
    assert len(results) >= 1
    assert any(r.name == "my-org/code-reviewer" for r in results)


def test_search_matches_tags(db: MetadataDB):
    db.insert_skill(
        _make_skill(
            description="A security scanner",
            tags=["security", "scanner"],
        )
    )
    results = db.search(query="security")
    assert len(results) >= 1


# -- namespace filter -------------------------------------------------------


def test_search_namespace_filter(db: MetadataDB):
    db.insert_skill(_make_skill())
    db.insert_skill(
        _make_skill(
            name="other-org/tool",
            namespace="other-org",
            version="1.0.0",
        )
    )

    results = db.search(namespace="my-org")
    assert all(r.namespace == "my-org" for r in results)
    assert len(results) == 1


# -- tag filter -------------------------------------------------------------


def test_search_tag_filter(db: MetadataDB):
    db.insert_skill(_make_skill(tags=["security", "code-review"]))
    db.insert_skill(
        _make_skill(
            name="my-org/linter",
            namespace="my-org",
            version="1.0.0",
            tags=["lint"],
        )
    )

    results = db.search(tag="security")
    assert len(results) == 1
    assert results[0].name == "my-org/code-reviewer"


# -- pagination -------------------------------------------------------------


def test_search_pagination(db: MetadataDB):
    for i in range(10):
        db.insert_skill(
            _make_skill(
                name=f"my-org/skill-{i}",
                namespace="my-org",
                version="1.0.0",
            )
        )

    page1 = db.search(limit=3, offset=0)
    page2 = db.search(limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) == 3
    # No overlap
    names1 = {r.name for r in page1}
    names2 = {r.name for r in page2}
    assert names1.isdisjoint(names2)


def test_count_search(db: MetadataDB):
    for i in range(5):
        db.insert_skill(
            _make_skill(
                name=f"my-org/skill-{i}",
                namespace="my-org",
                version="1.0.0",
            )
        )
    total = db.count_search()
    assert total == 5

    total_ns = db.count_search(namespace="my-org")
    assert total_ns == 5

    total_other = db.count_search(namespace="other-org")
    assert total_other == 0


# -- edge cases --------------------------------------------------------------


def test_search_empty_query_returns_all(db: MetadataDB):
    db.insert_skill(_make_skill())
    db.insert_skill(
        _make_skill(
            name="my-org/other",
            namespace="my-org",
            version="1.0.0",
        )
    )

    results = db.search(query=None)
    assert len(results) == 2

    results2 = db.search(query="")
    # Empty string treated same as None (no FTS filter)
    assert len(results2) == 2


def test_search_special_characters(db: MetadataDB):
    db.insert_skill(_make_skill(description="Handles C++ and C# code"))
    # FTS5 should not crash on special chars — we just verify no exception
    results = db.search(query="C++")
    # May or may not match depending on FTS tokenizer, but should not error
    assert isinstance(results, list)
