"""Small, explicit, transactional SQLite migration runner."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Migration:
    """One ordered migration for a named persistence component."""

    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


_CREATE_MIGRATIONS = """\
CREATE TABLE IF NOT EXISTS schema_migrations (
    component TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY(component, version)
);
"""


def apply_migrations(
    conn: sqlite3.Connection,
    component: str,
    migrations: Sequence[Migration],
) -> None:
    """Apply pending migrations exactly once, rolling back failed steps."""
    conn.execute(_CREATE_MIGRATIONS)
    applied = {
        row[0]
        for row in conn.execute(
            "SELECT version FROM schema_migrations WHERE component = ?",
            (component,),
        ).fetchall()
    }

    previous = 0
    for migration in migrations:
        if migration.version <= previous:
            raise ValueError(f"Migrations for {component!r} must be strictly ordered")
        previous = migration.version
        if migration.version in applied:
            continue

        conn.execute("SAVEPOINT skillctl_schema_migration")
        try:
            migration.apply(conn)
            conn.execute(
                """INSERT INTO schema_migrations
                   (component, version, name, applied_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    component,
                    migration.version,
                    migration.name,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.execute("RELEASE SAVEPOINT skillctl_schema_migration")
        except BaseException:
            conn.execute("ROLLBACK TO SAVEPOINT skillctl_schema_migration")
            conn.execute("RELEASE SAVEPOINT skillctl_schema_migration")
            raise
    conn.commit()


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return whether *table* currently has *column*."""
    return column in {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
