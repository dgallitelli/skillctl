"""Experimental queries over caller-recorded lineage and optional audit files.

Results are only as complete and trustworthy as the records supplied by the
embedding application.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from skillctl.lineage.store import LineageStore


def _to_epoch(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


class ForensicQuery:
    def __init__(self, lineage_store: LineageStore, audit_log_path: Optional[str] = None):
        self.lineage = lineage_store
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None

    # -- lineage-backed queries ---------------------------------------------

    def invocations_accessing(
        self,
        *,
        skill: Optional[str] = None,
        label: Optional[str] = None,
        data_ref: Optional[str] = None,
        since=None,
        until=None,
    ) -> list[dict]:
        """Distinct invocations matching the filters (skill / data label / ref / window)."""
        rows = self.lineage.query(skill=skill, label=label, since=_to_epoch(since), until=_to_epoch(until))
        if data_ref is not None:
            rows = [r for r in rows if r["data_ref"] == data_ref]
        seen: dict[str, dict] = {}
        for r in rows:
            inv = r["invocation_id"]
            entry = seen.setdefault(inv, {"invocation_id": inv, "skill": r["skill"], "actor": r["actor"], "data": []})
            entry["data"].append(
                {"ref": r["data_ref"], "label": r["data_label"], "relation": r["relation"], "ts": r["ts"]}
            )
        return list(seen.values())

    def who_accessed(self, data_ref: str, since=None, until=None) -> list[str]:
        return self.lineage.who_accessed(data_ref, _to_epoch(since), _to_epoch(until))

    def provenance(self, data_ref: str) -> dict:
        return {"output": data_ref, "sources": sorted(self.lineage.trace_provenance(data_ref))}

    def downstream(self, data_ref: str) -> list[dict]:
        return self.lineage.downstream_consumers(data_ref)

    # -- audit-backed queries -----------------------------------------------

    def skill_activity(self, skill: str, since=None, until=None) -> list[dict]:
        """Audit-log events for a skill within a time window."""
        if not self.audit_log_path or not self.audit_log_path.is_file():
            return []
        s, u = _to_epoch(since), _to_epoch(until)
        out = []
        for line in self.audit_log_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if skill not in e.get("resource", ""):
                continue
            ts = _to_epoch(e.get("timestamp"))
            if s is not None and (ts is None or ts < s):
                continue
            if u is not None and (ts is None or ts > u):
                continue
            out.append(e)
        return out
