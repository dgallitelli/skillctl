"""SQLite-backed counter store for runtime policy hooks.

Persists rate-limit counters across restarts and works in multi-process
deployments (SQLite WAL). Async method signatures keep the hook interface
async-first; the SQLite calls themselves are fast and synchronous.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional, Union

_CREATE_COUNTERS = """\
CREATE TABLE IF NOT EXISTS rate_limit_counters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key TEXT NOT NULL,
    ts INTEGER NOT NULL
);
"""

_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_rlc_key_ts ON rate_limit_counters(scope_key, ts);"


class PolicyStore:
    """Counter storage for rate limiting (and future counter-based hooks)."""

    def __init__(self, db_path: Union[str, Path] = ":memory:", *, conn: Optional[sqlite3.Connection] = None) -> None:
        self._lock = threading.Lock()
        if conn is not None:
            self._conn = conn
            self._owns = False
        else:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._owns = True
        self._conn.row_factory = sqlite3.Row

    def initialize(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.executescript(_CREATE_COUNTERS + _CREATE_INDEX)
            self._conn.commit()

    async def count_in_window(self, scope_key: str, start: int, end: int) -> int:
        """Count invocations recorded for *scope_key* in [start, end]."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM rate_limit_counters WHERE scope_key = ? AND ts >= ? AND ts <= ?",
                (scope_key, start, end),
            ).fetchone()
        return int(row[0])

    async def increment(self, scope_key: str, now: int) -> None:
        """Record one invocation for *scope_key* at time *now* (epoch seconds)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO rate_limit_counters (scope_key, ts) VALUES (?, ?)",
                (scope_key, now),
            )
            self._conn.commit()

    async def prune(self, older_than: int) -> int:
        """Delete counter rows older than *older_than* (epoch seconds)."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM rate_limit_counters WHERE ts < ?", (older_than,))
            self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        if self._owns:
            self._conn.close()
