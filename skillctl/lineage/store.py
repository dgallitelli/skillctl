"""Experimental caller-populated data lineage store.

Records what an embedding application reports that an invocation read and
wrote. SkillsOps does not collect or verify this information automatically.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional, Union

_CREATE = """\
CREATE TABLE IF NOT EXISTS lineage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_id TEXT NOT NULL,
    data_ref TEXT NOT NULL,
    data_label TEXT DEFAULT '',
    relation TEXT NOT NULL,            -- 'read' | 'write'
    skill TEXT NOT NULL,
    actor TEXT DEFAULT '',
    ts INTEGER NOT NULL,
    environment TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_lineage_data ON lineage(data_ref);
CREATE INDEX IF NOT EXISTS idx_lineage_skill ON lineage(skill);
CREATE INDEX IF NOT EXISTS idx_lineage_inv ON lineage(invocation_id);
CREATE INDEX IF NOT EXISTS idx_lineage_ts ON lineage(ts);
"""


def _norm_items(items) -> list[tuple[str, str]]:
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append((it, ""))
        elif isinstance(it, dict):
            out.append((it["ref"], it.get("label", "")))
        else:
            out.append((it[0], it[1] if len(it) > 1 else ""))
    return out


class LineageStore:
    def __init__(self, db_path: Union[str, Path] = ":memory:", *, conn: Optional[sqlite3.Connection] = None) -> None:
        if conn is not None:
            self._conn = conn
            self._owns = False
        else:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._owns = True
        self._conn.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self._conn.executescript(_CREATE)
        self._conn.commit()

    def record_access(
        self,
        *,
        invocation_id: str,
        skill: str,
        actor: str = "",
        reads=None,
        writes=None,
        ts: Optional[int] = None,
        environment: str = "",
    ) -> None:
        ts = ts if ts is not None else int(time.time())
        rows = []
        for ref, label in _norm_items(reads):
            rows.append((invocation_id, ref, label, "read", skill, actor, ts, environment))
        for ref, label in _norm_items(writes):
            rows.append((invocation_id, ref, label, "write", skill, actor, ts, environment))
        self._conn.executemany(
            "INSERT INTO lineage (invocation_id, data_ref, data_label, relation, skill, actor, ts, environment) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        self._conn.commit()

    def _rows(self, where: str, params: tuple) -> list[dict]:
        return [dict(r) for r in self._conn.execute(f"SELECT * FROM lineage WHERE {where}", params).fetchall()]

    def accessed_in_invocation(self, invocation_id: str) -> list[dict]:
        return self._rows("invocation_id = ? ORDER BY id", (invocation_id,))

    def accesses_of(self, data_ref: str, since: Optional[int] = None, until: Optional[int] = None) -> list[dict]:
        where = "data_ref = ?"
        params: list = [data_ref]
        if since is not None:
            where += " AND ts >= ?"
            params.append(since)
        if until is not None:
            where += " AND ts <= ?"
            params.append(until)
        return self._rows(where + " ORDER BY ts", tuple(params))

    def who_accessed(self, data_ref: str, since: Optional[int] = None, until: Optional[int] = None) -> list[str]:
        return sorted({r["actor"] for r in self.accesses_of(data_ref, since, until) if r["actor"]})

    def downstream_consumers(self, data_ref: str) -> list[dict]:
        """Skills/outputs produced in invocations that read *data_ref*."""
        reads = self._rows("data_ref = ? AND relation = 'read'", (data_ref,))
        inv_ids = {r["invocation_id"] for r in reads}
        if not inv_ids:
            return []
        placeholders = ",".join("?" * len(inv_ids))
        writes = self._rows(f"relation = 'write' AND invocation_id IN ({placeholders})", tuple(inv_ids))
        return writes

    def trace_provenance(self, data_ref: str, _seen: Optional[set] = None) -> set[str]:
        """Return the set of upstream source data refs that fed into *data_ref*."""
        seen = _seen if _seen is not None else set()
        writes = self._rows("data_ref = ? AND relation = 'write'", (data_ref,))
        sources: set[str] = set()
        for w in writes:
            inputs = self._rows("invocation_id = ? AND relation = 'read'", (w["invocation_id"],))
            for inp in inputs:
                ref = inp["data_ref"]
                if ref in seen:
                    continue
                seen.add(ref)
                sources.add(ref)
                sources |= self.trace_provenance(ref, seen)
        return sources

    def query(
        self,
        *,
        skill: Optional[str] = None,
        label: Optional[str] = None,
        relation: Optional[str] = None,
        actor: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
    ) -> list[dict]:
        where = "1=1"
        params: list = []
        for col, val in (("skill", skill), ("data_label", label), ("relation", relation), ("actor", actor)):
            if val is not None:
                where += f" AND {col} = ?"
                params.append(val)
        if since is not None:
            where += " AND ts >= ?"
            params.append(since)
        if until is not None:
            where += " AND ts <= ?"
            params.append(until)
        return self._rows(where + " ORDER BY ts", tuple(params))

    def close(self) -> None:
        if self._owns:
            self._conn.close()
