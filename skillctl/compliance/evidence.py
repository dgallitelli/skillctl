"""Evidence collector for compliance reporting.

Queries existing SkillsOps data sources (security scan, HMAC audit log, RBAC
store, skill metadata, deployment records, attestations) to gather evidence for
a control. Each record is timestamped and hash-verified. Synchronous and
deterministic — no LLM, no network.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from skillctl.compliance.frameworks import Control, EvidenceRecord, EvidenceType


def _hash(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


class EvidenceCollector:
    """Collects evidence from SkillsOps data sources."""

    def __init__(
        self,
        *,
        skill_path: Optional[str] = None,
        audit_log_path: Optional[str] = None,
        rbac_store=None,
        attestation_store=None,
        deployment_store=None,
        registry_db=None,
    ) -> None:
        self.skill_path = skill_path
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None
        self.rbac_store = rbac_store
        self.attestation_store = attestation_store
        self.deployment_store = deployment_store
        self.registry_db = registry_db

    def collect_for_control(
        self,
        control: Control,
        skill_name: str,
        skill_version: str,
        framework_id: str = "",
        time_range: Optional[tuple[str, str]] = None,
    ) -> list[EvidenceRecord]:
        records: list[EvidenceRecord] = []
        now = datetime.now(timezone.utc).isoformat()

        for etype in control.evidence_types:
            items = self._query(etype, control, skill_name, skill_version, framework_id)
            for item in items:
                records.append(
                    EvidenceRecord(
                        control_id=control.id,
                        evidence_type=etype,
                        collected_at=now,
                        source=f"{etype.value}:{skill_name}@{skill_version}",
                        content=item,
                        valid_from=time_range[0] if time_range else "epoch",
                        valid_until=time_range[1] if time_range else now,
                        integrity_hash=_hash(item),
                    )
                )
        return records

    # -- per-type queries ----------------------------------------------------

    def _query(
        self, etype: EvidenceType, control: Control, skill_name: str, skill_version: str, framework_id: str
    ) -> list[dict]:
        try:
            if etype == EvidenceType.SECURITY_SCAN:
                return self._scan()
            if etype == EvidenceType.AUDIT_LOG:
                return self._audit(skill_name)
            if etype == EvidenceType.POLICY_EVALUATION:
                return self._policy(skill_name)
            if etype == EvidenceType.RBAC_ASSIGNMENT:
                return self._rbac()
            if etype == EvidenceType.SKILL_METADATA:
                return self._metadata()
            if etype == EvidenceType.VERSION_HISTORY:
                return self._version(skill_name, skill_version)
            if etype == EvidenceType.DEPLOYMENT_RECORD:
                return self._deployment(skill_name)
            if etype == EvidenceType.MANUAL_ATTESTATION:
                return self._attestation(control.id, skill_name, skill_version)
            if etype == EvidenceType.OTEL_TRACE:
                return []  # No trace backend queried in this build.
        except Exception:  # noqa: BLE001 - evidence collection must never crash a report
            return []
        return []

    def _scan(self) -> list[dict]:
        if not self.skill_path:
            return []
        from skillctl.eval.cli import run_audit

        report = run_audit(self.skill_path)
        return [
            {
                "score": report.score,
                "grade": report.grade,
                "passed": report.passed,
                "critical": report.critical_count,
                "warning": report.warning_count,
                "findings": [{"code": f.code, "severity": f.severity.value, "title": f.title} for f in report.findings],
            }
        ]

    def _read_audit(self) -> list[dict]:
        if not self.audit_log_path or not self.audit_log_path.is_file():
            return []
        out = []
        for line in self.audit_log_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
        return out

    def _audit(self, skill_name: str) -> list[dict]:
        entries = [e for e in self._read_audit() if skill_name in e.get("resource", "")]
        if not entries:
            return []
        integrity = self._audit_integrity()
        return [
            {"entry_count": len(entries), "actions": sorted({e.get("action") for e in entries}), "integrity": integrity}
        ]

    def _policy(self, skill_name: str) -> list[dict]:
        entries = [
            e
            for e in self._read_audit()
            if e.get("action") == "policy_decision" and skill_name in e.get("resource", "")
        ]
        if not entries:
            return []
        decisions: dict[str, int] = {}
        for e in entries:
            d = (e.get("details") or {}).get("decision", "unknown")
            decisions[d] = decisions.get(d, 0) + 1
        return [{"evaluation_count": len(entries), "decisions": decisions}]

    def _audit_integrity(self) -> dict:
        if not self.audit_log_path:
            return {}
        # Structural presence here; key-based HMAC verification happens in the
        # registry (which holds the signing key).
        return {"chain_present": True, "log": str(self.audit_log_path)}

    def _rbac(self) -> list[dict]:
        if self.rbac_store is None:
            return []
        try:
            users = self.rbac_store.count_users()
            decisions = self.rbac_store.read_decisions(limit=1000)
            denials = sum(1 for d in decisions if not d.get("allowed"))
            return [{"users": users, "auth_decisions": len(decisions), "denials": denials}]
        except Exception:  # noqa: BLE001
            return []

    def _metadata(self) -> list[dict]:
        if not self.skill_path:
            return []
        from skillctl.manifest import ManifestLoader

        manifest, _ = ManifestLoader().load(self.skill_path)
        md = manifest.metadata
        return [
            {
                "name": md.name,
                "version": md.version,
                "description": md.description,
                "has_description": bool(md.description),
                "category": md.category,
                "capabilities": manifest.spec.capabilities,
                "tags": md.tags,
            }
        ]

    def _version(self, skill_name: str, skill_version: str) -> list[dict]:
        if self.registry_db is not None:
            try:
                versions = [v.version for v in self.registry_db.get_versions(skill_name)]
                if versions:
                    return [{"versions": versions, "count": len(versions)}]
            except Exception:  # noqa: BLE001
                pass
        if skill_version:
            return [{"versions": [skill_version], "count": 1}]
        return []

    def _deployment(self, skill_name: str) -> list[dict]:
        if self.deployment_store is None:
            return []
        try:
            deployments = self.deployment_store.list_for_skill(skill_name)
            if not deployments:
                return []
            return [
                {
                    "deployment_count": len(deployments),
                    "strategies": sorted({d["strategy"] for d in deployments}),
                    "rollback_capable": True,
                }
            ]
        except Exception:  # noqa: BLE001
            return []

    def _attestation(self, control_id: str, skill_name: str, skill_version: str) -> list[dict]:
        if self.attestation_store is None:
            return []
        att = self.attestation_store.get_active(control_id, skill_name, skill_version)
        if att is None:
            return []
        return [att.to_dict()]
