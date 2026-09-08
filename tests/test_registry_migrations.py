"""Upgrade and rollback coverage for explicit registry migrations."""

from __future__ import annotations

import sqlite3

import pytest

from skillctl.registry.db import MetadataDB
from skillctl.registry.migrations import Migration, apply_migrations
from skillctl.registry.rbac.store import RBACStore


_LEGACY_SCHEMA = """\
CREATE TABLE skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    namespace TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    authors TEXT NOT NULL DEFAULT '[]',
    license TEXT,
    eval_grade TEXT,
    eval_score REAL,
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(name, version)
);
CREATE TABLE tokens (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    permissions TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT
);
"""


def test_legacy_registry_upgrade_preserves_rows_and_rebuilds_search(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_LEGACY_SCHEMA)
    conn.execute(
        """INSERT INTO skills
           (name, namespace, skill_name, version, description, content_hash,
            tags, authors, manifest_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "legacy/searchable",
            "legacy",
            "searchable",
            "1.0.0",
            "Existing searchable migration record",
            "ab" * 32,
            "[]",
            "[]",
            "{}",
            "2025-01-01T00:00:00+00:00",
            "2025-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    db = MetadataDB(db_path)
    db.initialize()
    try:
        record = db.get_skill("legacy/searchable", "1.0.0")
        assert record is not None
        assert record.status == "published"
        assert record.artifact_hash is None
        assert [item.name for item in db.search(query="migration")] == ["legacy/searchable"]
        versions = db.conn.execute(
            "SELECT version FROM schema_migrations WHERE component = 'registry' ORDER BY version"
        ).fetchall()
        assert [row[0] for row in versions] == [1, 2, 3]
    finally:
        db.close()


def test_rbac_upgrade_records_identity_token_migration(tmp_path):
    db_path = tmp_path / "legacy-rbac.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_LEGACY_SCHEMA)
    conn.commit()
    conn.close()

    db = MetadataDB(db_path)
    db.initialize()
    try:
        store = RBACStore(db.conn)
        store.initialize()
        columns = {row[1] for row in db.conn.execute("PRAGMA table_info(tokens)")}
        assert {"user_id", "scopes", "last_used_at"} <= columns
        migration = db.conn.execute(
            "SELECT name FROM schema_migrations WHERE component = 'rbac' AND version = 1"
        ).fetchone()
        assert migration[0] == "add-identity-token-columns"
    finally:
        db.close()


def test_failed_migration_rolls_back_schema_and_version_record():
    conn = sqlite3.connect(":memory:")

    def fail_after_ddl(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE should_rollback (id INTEGER)")
        raise RuntimeError("migration failed")

    with pytest.raises(RuntimeError, match="migration failed"):
        apply_migrations(conn, "test", [Migration(1, "fails", fail_after_ddl)])

    table = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'should_rollback'").fetchone()
    recorded = conn.execute("SELECT version FROM schema_migrations WHERE component = 'test'").fetchall()
    assert table is None
    assert recorded == []
