"""SQLite-backed store for deployments and invocation metrics."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional, Union

from skillctl.deployment.models import Deployment, DeploymentState, DeploymentStrategy

_CREATE = """\
CREATE TABLE IF NOT EXISTS deployments (
    id TEXT PRIMARY KEY,
    skill_name TEXT NOT NULL,
    skill_namespace TEXT NOT NULL,
    from_version TEXT,
    to_version TEXT NOT NULL,
    strategy TEXT NOT NULL,
    state TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    current_stage INTEGER NOT NULL DEFAULT 0,
    current_traffic_percent REAL NOT NULL DEFAULT 0.0,
    started_at TEXT,
    completed_at TEXT,
    initiated_by TEXT DEFAULT '',
    approved_by TEXT NOT NULL DEFAULT '[]',
    rolled_back_by TEXT,
    rollback_reason TEXT,
    bluegreen_active TEXT DEFAULT 'blue'
);
CREATE TABLE IF NOT EXISTS deployment_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT NOT NULL,
    version TEXT NOT NULL,
    ts INTEGER NOT NULL,
    success INTEGER NOT NULL,
    denied INTEGER NOT NULL DEFAULT 0,
    error INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dep_skill ON deployments(skill_name);
CREATE INDEX IF NOT EXISTS idx_metrics_dep ON deployment_metrics(deployment_id, ts);
"""


def _row_to_deployment(row: sqlite3.Row) -> Deployment:
    return Deployment(
        id=row["id"],
        skill_name=row["skill_name"],
        skill_namespace=row["skill_namespace"],
        from_version=row["from_version"],
        to_version=row["to_version"],
        strategy=DeploymentStrategy(row["strategy"]),
        state=DeploymentState(row["state"]),
        config=json.loads(row["config"]),
        current_stage=row["current_stage"],
        current_traffic_percent=row["current_traffic_percent"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        initiated_by=row["initiated_by"],
        approved_by=json.loads(row["approved_by"]),
        rolled_back_by=row["rolled_back_by"],
        rollback_reason=row["rollback_reason"],
    )


class DeploymentStore:
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

    def save(self, d: Deployment, *, bluegreen_active: str = "blue") -> None:
        self._conn.execute(
            """INSERT INTO deployments
               (id, skill_name, skill_namespace, from_version, to_version, strategy, state, config,
                current_stage, current_traffic_percent, started_at, completed_at, initiated_by,
                approved_by, rolled_back_by, rollback_reason, bluegreen_active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 state=excluded.state, config=excluded.config, current_stage=excluded.current_stage,
                 current_traffic_percent=excluded.current_traffic_percent, completed_at=excluded.completed_at,
                 approved_by=excluded.approved_by, rolled_back_by=excluded.rolled_back_by,
                 rollback_reason=excluded.rollback_reason, bluegreen_active=excluded.bluegreen_active""",
            (
                d.id,
                d.skill_name,
                d.skill_namespace,
                d.from_version,
                d.to_version,
                d.strategy.value,
                d.state.value,
                json.dumps(d.config),
                d.current_stage,
                d.current_traffic_percent,
                d.started_at,
                d.completed_at,
                d.initiated_by,
                json.dumps(d.approved_by),
                d.rolled_back_by,
                d.rollback_reason,
                bluegreen_active,
            ),
        )
        self._conn.commit()

    def get(self, deployment_id: str) -> Optional[Deployment]:
        row = self._conn.execute("SELECT * FROM deployments WHERE id = ?", (deployment_id,)).fetchone()
        return _row_to_deployment(row) if row else None

    def get_bluegreen_active(self, deployment_id: str) -> str:
        row = self._conn.execute("SELECT bluegreen_active FROM deployments WHERE id = ?", (deployment_id,)).fetchone()
        return row["bluegreen_active"] if row else "blue"

    def active_for_skill(self, skill_name: str) -> Optional[Deployment]:
        """The most recent deployment for a skill (drives routing).

        Returns the latest deployment regardless of state: a COMPLETED one means
        the new version is fully live; a ROLLED_BACK one means the old version is
        served; an in-progress one drives the traffic split.
        """
        row = self._conn.execute(
            "SELECT * FROM deployments WHERE skill_name = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (skill_name,),
        ).fetchone()
        return _row_to_deployment(row) if row else None

    def list_for_skill(self, skill_name: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM deployments WHERE skill_name = ? ORDER BY started_at DESC", (skill_name,)
        ).fetchall()
        return [_row_to_deployment(r).to_dict() for r in rows]

    def list_all(self) -> list[Deployment]:
        rows = self._conn.execute("SELECT * FROM deployments ORDER BY started_at DESC").fetchall()
        return [_row_to_deployment(r) for r in rows]

    # -- metrics -------------------------------------------------------------

    def record_metric(
        self,
        deployment_id: str,
        version: str,
        ts: int,
        *,
        success: bool,
        denied: bool = False,
        error: bool = False,
        latency_ms: float = 0,
    ) -> None:
        self._conn.execute(
            "INSERT INTO deployment_metrics (deployment_id, version, ts, success, denied, error, latency_ms) VALUES (?,?,?,?,?,?,?)",
            (deployment_id, version, ts, 1 if success else 0, 1 if denied else 0, 1 if error else 0, latency_ms),
        )
        self._conn.commit()

    def metrics_in_window(self, deployment_id: str, version: str, start_ts: int, end_ts: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM deployment_metrics WHERE deployment_id = ? AND version = ? AND ts >= ? AND ts <= ?",
            (deployment_id, version, start_ts, end_ts),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        if self._owns:
            self._conn.close()
