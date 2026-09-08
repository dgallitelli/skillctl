"""SQLite metadata index — MetadataDB.

Manages the SQLite database containing the ``skills`` table, ``skills_fts``
FTS5 virtual table for full-text search, and ``tokens`` table for API token
storage.  Provides CRUD operations, search, and version history queries.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from skillctl.registry.migrations import Migration, apply_migrations, has_column

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SkillRecord:
    """One row in the ``skills`` table."""

    id: int | None
    name: str  # "my-org/code-reviewer"
    namespace: str  # "my-org"
    version: str
    description: str
    content_hash: str
    artifact_hash: str | None = None
    tags: list[str] = field(default_factory=list)
    authors: list[dict] = field(default_factory=list)
    license: str | None = None
    eval_grade: str | None = None  # A-F or None
    eval_score: float | None = None  # 0-100 or None
    status: str = "published"  # "draft" | "published" (RBAC create/publish split)
    created_at: str = ""  # ISO 8601
    updated_at: str = ""
    manifest_json: str = "{}"


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

_CREATE_SKILLS = """\
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    namespace TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    artifact_hash TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    authors TEXT NOT NULL DEFAULT '[]',
    license TEXT,
    eval_grade TEXT,
    eval_score REAL,
    status TEXT NOT NULL DEFAULT 'published',
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(name, version)
);
"""

_CREATE_FTS = """\
CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts USING fts5(
    name, description, tags,
    content=skills,
    content_rowid=id
);
"""

_TRIGGER_AI = """\
CREATE TRIGGER IF NOT EXISTS skills_ai AFTER INSERT ON skills BEGIN
    INSERT INTO skills_fts(rowid, name, description, tags)
    VALUES (new.id, new.name, new.description, new.tags);
END;
"""

_TRIGGER_AD = """\
CREATE TRIGGER IF NOT EXISTS skills_ad AFTER DELETE ON skills BEGIN
    INSERT INTO skills_fts(skills_fts, rowid, name, description, tags)
    VALUES ('delete', old.id, old.name, old.description, old.tags);
END;
"""

_TRIGGER_AU = """\
CREATE TRIGGER IF NOT EXISTS skills_au AFTER UPDATE ON skills BEGIN
    INSERT INTO skills_fts(skills_fts, rowid, name, description, tags)
    VALUES ('delete', old.id, old.name, old.description, old.tags);
    INSERT INTO skills_fts(rowid, name, description, tags)
    VALUES (new.id, new.name, new.description, new.tags);
END;
"""

_CREATE_TOKENS = """\
CREATE TABLE IF NOT EXISTS tokens (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    permissions TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_skills_namespace ON skills(namespace);",
    "CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name);",
    "CREATE INDEX IF NOT EXISTS idx_skills_created ON skills(created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_tokens_hash ON tokens(token_hash);",
]


def _add_status_column(conn: sqlite3.Connection) -> None:
    if not has_column(conn, "skills", "status"):
        conn.execute("ALTER TABLE skills ADD COLUMN status TEXT NOT NULL DEFAULT 'published'")


def _add_artifact_hash_column(conn: sqlite3.Connection) -> None:
    if not has_column(conn, "skills", "artifact_hash"):
        conn.execute("ALTER TABLE skills ADD COLUMN artifact_hash TEXT")


def _rebuild_fts_index(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO skills_fts(skills_fts) VALUES ('rebuild')")


_MIGRATIONS = (
    Migration(1, "add-skill-lifecycle-status", _add_status_column),
    Migration(2, "add-complete-artifact-hash", _add_artifact_hash_column),
    Migration(3, "rebuild-existing-skill-search-index", _rebuild_fts_index),
)


# ---------------------------------------------------------------------------
# MetadataDB
# ---------------------------------------------------------------------------


class MetadataDB:
    """SQLite-backed metadata index for skills and tokens."""

    def __init__(self, db_path: Path | str, check_same_thread: bool = True) -> None:
        if isinstance(db_path, str) and db_path == ":memory:":
            self._db_path = ":memory:"
        else:
            self._db_path = str(db_path)
        self._check_same_thread = check_same_thread
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    # -- lifecycle -----------------------------------------------------------

    def initialize(self) -> None:
        """Create tables, FTS5 index, triggers, and indexes.  Idempotent."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
            self._conn = sqlite3.connect(self._db_path, check_same_thread=self._check_same_thread)
            self._conn.row_factory = sqlite3.Row
            # WAL mode for concurrent read performance and a bounded wait for
            # another writer instead of immediate "database is locked" errors.
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.execute("PRAGMA busy_timeout=5000;")

            self._conn.executescript(
                _CREATE_SKILLS + _CREATE_FTS + _TRIGGER_AI + _TRIGGER_AD + _TRIGGER_AU + _CREATE_TOKENS
            )
            for idx_sql in _CREATE_INDEXES:
                self._conn.execute(idx_sql)
            apply_migrations(self._conn, "registry", _MIGRATIONS)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialized — call initialize() first")
        return self._conn

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> SkillRecord:
        return SkillRecord(
            id=row["id"],
            name=row["name"],
            namespace=row["namespace"],
            version=row["version"],
            description=row["description"],
            content_hash=row["content_hash"],
            artifact_hash=row["artifact_hash"] if "artifact_hash" in row.keys() else None,
            tags=json.loads(row["tags"]),
            authors=json.loads(row["authors"]),
            license=row["license"],
            eval_grade=row["eval_grade"],
            eval_score=row["eval_score"],
            status=row["status"] if "status" in row.keys() else "published",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            manifest_json=row["manifest_json"],
        )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    # -- CRUD ----------------------------------------------------------------

    def insert_skill(self, skill: SkillRecord) -> int:
        """Insert a skill record.  Returns the new row id."""
        now = self._now_iso()
        created = skill.created_at or now
        updated = skill.updated_at or now
        # Extract skill_name from the full name (namespace/skill_name)
        parts = skill.name.split("/", 1)
        skill_name = parts[1] if len(parts) == 2 else skill.name

        with self._lock:
            try:
                cur = self.conn.execute(
                    """INSERT INTO skills
                       (name, namespace, skill_name, version, description,
                        content_hash, artifact_hash, tags, authors, license,
                        eval_grade, eval_score, status, manifest_json,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        skill.name,
                        skill.namespace,
                        skill_name,
                        skill.version,
                        skill.description,
                        skill.content_hash,
                        skill.artifact_hash,
                        json.dumps(skill.tags),
                        json.dumps(skill.authors),
                        skill.license,
                        skill.eval_grade,
                        skill.eval_score,
                        skill.status,
                        skill.manifest_json,
                        created,
                        updated,
                    ),
                )
                self.conn.commit()
                return cur.lastrowid  # type: ignore[return-value]
            except BaseException:
                self.conn.rollback()
                raise

    def set_skill_status(self, name: str, version: str, status: str) -> bool:
        """Compatibility setter. Prefer :meth:`transition_skill_status`."""
        if status not in {"draft", "published"}:
            raise ValueError(f"Invalid skill status: {status}")
        with self._lock:
            cur = self.conn.execute(
                "UPDATE skills SET status = ?, updated_at = ? WHERE name = ? AND version = ?",
                (status, self._now_iso(), name, version),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def transition_skill_status(
        self,
        name: str,
        version: str,
        *,
        expected: str,
        target: str,
    ) -> str:
        """Atomically compare-and-set lifecycle status.

        Returns ``"transitioned"``, ``"already"``, ``"conflict"``, or
        ``"missing"``.
        """
        valid = {"draft", "published"}
        if expected not in valid or target not in valid or expected == target:
            raise ValueError(f"Invalid lifecycle transition: {expected!r} -> {target!r}")
        with self._lock:
            cur = self.conn.execute(
                """UPDATE skills
                   SET status = ?, updated_at = ?
                   WHERE name = ? AND version = ? AND status = ?""",
                (target, self._now_iso(), name, version, expected),
            )
            if cur.rowcount == 1:
                self.conn.commit()
                return "transitioned"
            row = self.conn.execute(
                "SELECT status FROM skills WHERE name = ? AND version = ?",
                (name, version),
            ).fetchone()
            self.conn.commit()
        if row is None:
            return "missing"
        if row["status"] == target:
            return "already"
        return "conflict"

    def get_skill(self, name: str, version: str) -> SkillRecord | None:
        """Fetch a single skill by full name and version."""
        row = self.conn.execute(
            "SELECT * FROM skills WHERE name = ? AND version = ?",
            (name, version),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def get_versions(self, name: str) -> list[SkillRecord]:
        """Return all versions of a skill ordered by created_at DESC."""
        rows = self.conn.execute(
            "SELECT * FROM skills WHERE name = ? ORDER BY created_at DESC",
            (name,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def delete_skill(self, name: str, version: str) -> bool:
        """Delete a skill version.  Returns True if a row was deleted."""
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM skills WHERE name = ? AND version = ?",
                (name, version),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def update_eval(self, name: str, version: str, grade: str, score: float) -> bool:
        """Attach eval grade/score to a skill version.  Returns True if updated."""
        now = self._now_iso()
        with self._lock:
            cur = self.conn.execute(
                """UPDATE skills
                   SET eval_grade = ?, eval_score = ?, updated_at = ?
                   WHERE name = ? AND version = ?""",
                (grade, score, now, name, version),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def referenced_blob_hashes(self) -> set[str]:
        """Return all content and artifact digests referenced by metadata."""
        rows = self.conn.execute("SELECT content_hash, artifact_hash FROM skills").fetchall()
        return {digest for row in rows for digest in (row["content_hash"], row["artifact_hash"]) if digest}

    # -- search --------------------------------------------------------------

    def search(
        self,
        query: str | None = None,
        namespace: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SkillRecord]:
        """Full-text search with optional namespace/tag filters and pagination."""
        # Clamp pagination bounds
        limit = max(1, min(limit, 500))
        offset = max(0, min(offset, 100_000))
        # Treat empty string as no query
        q = query if query and query.strip() else None
        result = self._build_search(q, namespace, tag, status, limit, offset, count_only=False)
        assert isinstance(result, list)
        return result

    def count_search(
        self,
        query: str | None = None,
        namespace: str | None = None,
        tag: str | None = None,
        status: str | None = None,
    ) -> int:
        """Return total count matching the same filters (for pagination)."""
        q = query if query and query.strip() else None
        result = self._build_search(q, namespace, tag, status, limit=0, offset=0, count_only=True)
        assert isinstance(result, int)
        return result

    @staticmethod
    def _sanitize_fts_query(query: str) -> str | None:
        """Escape special FTS5 characters so arbitrary user input is safe.

        Returns None if the query has no searchable tokens.
        """
        tokens = query.split()
        if not tokens:
            return None
        return " ".join(f'"{t.replace(chr(34), chr(34) + chr(34))}"' for t in tokens)

    def _build_search(
        self,
        query: str | None,
        namespace: str | None,
        tag: str | None,
        status: str | None,
        limit: int,
        offset: int,
        count_only: bool,
    ) -> list[SkillRecord] | int:  # type: ignore[return]
        params: list = []
        where_clauses: list[str] = []

        sanitized = self._sanitize_fts_query(query) if query else None
        if sanitized:
            base = "FROM skills JOIN skills_fts ON skills.id = skills_fts.rowid"
            where_clauses.append("skills_fts MATCH ?")
            params.append(sanitized)
        else:
            base = "FROM skills"

        if namespace:
            where_clauses.append("skills.namespace = ?")
            params.append(namespace)

        if tag:
            escaped_tag = tag.replace("%", "\\%").replace("_", "\\_")
            where_clauses.append("skills.tags LIKE ? ESCAPE '\\'")
            params.append(f'%"{escaped_tag}"%')

        if status:
            where_clauses.append("skills.status = ?")
            params.append(status)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        if count_only:
            sql = f"SELECT COUNT(*) {base}{where_sql}"
            row = self.conn.execute(sql, params).fetchone()
            return row[0]

        # ORDER BY: FTS5 rank when query present, then created_at DESC
        if query:
            order = "ORDER BY rank, skills.created_at DESC"
        else:
            order = "ORDER BY skills.created_at DESC"

        sql = f"SELECT skills.* {base}{where_sql} {order} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_record(r) for r in rows]
