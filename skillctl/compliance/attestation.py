"""Manual attestation system for compliance controls.

Some controls require human sign-off. Attestations are stored in SQLite, linked
to the HMAC audit chain, time-bounded, and invalidated when the skill version
changes.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Union


@dataclass
class Attestation:
    id: str
    control_id: str
    skill_name: str
    skill_version: str
    framework_id: str
    attested_by: str
    attested_at: str
    statement: str
    evidence_description: str = ""
    valid_until: str = ""
    superseded_by: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "control_id": self.control_id,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "framework_id": self.framework_id,
            "attested_by": self.attested_by,
            "attested_at": self.attested_at,
            "statement": self.statement,
            "evidence_description": self.evidence_description,
            "valid_until": self.valid_until,
            "superseded_by": self.superseded_by,
        }


_CREATE = """\
CREATE TABLE IF NOT EXISTS attestations (
    id TEXT PRIMARY KEY,
    control_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    skill_version TEXT NOT NULL,
    framework_id TEXT NOT NULL,
    attested_by TEXT NOT NULL,
    attested_at TEXT NOT NULL,
    statement TEXT NOT NULL,
    evidence_description TEXT DEFAULT '',
    valid_until TEXT NOT NULL,
    superseded_by TEXT
);
"""


class AttestationStore:
    """SQLite-backed storage for attestations."""

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

    def add(
        self,
        *,
        control_id: str,
        skill_name: str,
        skill_version: str,
        framework_id: str,
        attested_by: str,
        statement: str,
        evidence_description: str = "",
        expiry_days: int = 90,
    ) -> Attestation:
        now = datetime.now(timezone.utc)
        att = Attestation(
            id=str(uuid.uuid4()),
            control_id=control_id,
            skill_name=skill_name,
            skill_version=skill_version,
            framework_id=framework_id,
            attested_by=attested_by,
            attested_at=now.isoformat(),
            statement=statement,
            evidence_description=evidence_description,
            valid_until=(now + timedelta(days=expiry_days)).isoformat(),
        )
        # Supersede any prior active attestation for the same control+skill+version.
        self._conn.execute(
            """UPDATE attestations SET superseded_by = ?
               WHERE control_id = ? AND skill_name = ? AND skill_version = ? AND superseded_by IS NULL""",
            (att.id, control_id, skill_name, skill_version),
        )
        self._conn.execute(
            """INSERT INTO attestations
               (id, control_id, skill_name, skill_version, framework_id, attested_by, attested_at,
                statement, evidence_description, valid_until, superseded_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                att.id,
                control_id,
                skill_name,
                skill_version,
                framework_id,
                attested_by,
                att.attested_at,
                statement,
                evidence_description,
                att.valid_until,
            ),
        )
        self._conn.commit()
        return att

    def get_active(self, control_id: str, skill_name: str, skill_version: str) -> Optional[Attestation]:
        """Return a current (non-superseded, non-expired) attestation, if any."""
        row = self._conn.execute(
            """SELECT * FROM attestations
               WHERE control_id = ? AND skill_name = ? AND skill_version = ? AND superseded_by IS NULL
               ORDER BY attested_at DESC LIMIT 1""",
            (control_id, skill_name, skill_version),
        ).fetchone()
        if row is None:
            return None
        if row["valid_until"] and row["valid_until"] <= datetime.now(timezone.utc).isoformat():
            return None  # expired
        return Attestation(
            id=row["id"],
            control_id=row["control_id"],
            skill_name=row["skill_name"],
            skill_version=row["skill_version"],
            framework_id=row["framework_id"],
            attested_by=row["attested_by"],
            attested_at=row["attested_at"],
            statement=row["statement"],
            evidence_description=row["evidence_description"],
            valid_until=row["valid_until"],
            superseded_by=row["superseded_by"],
        )

    def close(self) -> None:
        if self._owns:
            self._conn.close()
