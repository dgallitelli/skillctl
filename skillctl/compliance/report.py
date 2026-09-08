"""Experimental, non-certifying compliance mapping report generator.

Maps SkillsOps governance controls to regulatory framework requirements and
produces deterministic previews (JSON or Markdown).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from skillctl.compliance.evidence import EvidenceCollector
from skillctl.compliance.frameworks import (
    ComplianceFramework,
    ComplianceStatus,
    Control,
    EvidenceRecord,
    EvidenceType,
    RiskLevel,
    SkillRiskClassification,
)

_STATUS_SCORE = {
    ComplianceStatus.COMPLIANT: 1.0,
    ComplianceStatus.PARTIALLY_COMPLIANT: 0.5,
    ComplianceStatus.NON_COMPLIANT: 0.0,
    ComplianceStatus.PENDING_REVIEW: 0.0,
}


@dataclass
class ControlAssessment:
    control: Control
    status: ComplianceStatus
    evidence: list[EvidenceRecord]
    evidence_evaluation: list[dict] = field(default_factory=list)
    gap_description: Optional[str] = None
    recommendation: Optional[str] = None


@dataclass
class ComplianceReport:
    skill_name: str
    skill_version: str
    framework_id: str
    framework_name: str
    generated_at: str
    generated_by: str
    risk_classification: Optional[SkillRiskClassification] = None
    assessments: list[ControlAssessment] = field(default_factory=list)

    @property
    def total_controls(self) -> int:
        return len(self.assessments)

    @property
    def compliant_count(self) -> int:
        return sum(1 for a in self.assessments if a.status == ComplianceStatus.COMPLIANT)

    @property
    def partial_count(self) -> int:
        return sum(1 for a in self.assessments if a.status == ComplianceStatus.PARTIALLY_COMPLIANT)

    @property
    def non_compliant_count(self) -> int:
        return sum(1 for a in self.assessments if a.status == ComplianceStatus.NON_COMPLIANT)

    @property
    def pending_count(self) -> int:
        return sum(1 for a in self.assessments if a.status == ComplianceStatus.PENDING_REVIEW)

    @property
    def compliance_score(self) -> float:
        if self.risk_classification and self.risk_classification.risk_level == RiskLevel.UNACCEPTABLE:
            return 0.0
        applicable = [a for a in self.assessments if a.status != ComplianceStatus.NOT_APPLICABLE]
        if not applicable:
            return 0.0
        total = sum(_STATUS_SCORE.get(a.status, 0.0) for a in applicable)
        return total / len(applicable)

    @property
    def gaps(self) -> list[ControlAssessment]:
        return [
            a
            for a in self.assessments
            if a.status
            in (
                ComplianceStatus.NON_COMPLIANT,
                ComplianceStatus.PARTIALLY_COMPLIANT,
                ComplianceStatus.PENDING_REVIEW,
            )
        ]

    @property
    def pending_attestations(self) -> list[ControlAssessment]:
        return [
            a
            for a in self.assessments
            if a.control.human_review_required and a.status == ComplianceStatus.PENDING_REVIEW
        ]


class ComplianceReportGenerator:
    """Generates compliance reports by collecting and evaluating evidence."""

    def __init__(self, evidence_collector: EvidenceCollector, frameworks: dict[str, ComplianceFramework]):
        self.collector = evidence_collector
        self.frameworks = frameworks

    def generate(
        self,
        skill_name: str,
        skill_version: str,
        framework_id: str,
        time_range: Optional[tuple[str, str]] = None,
        risk_classification: Optional[SkillRiskClassification] = None,
        generated_by: str = "system",
    ) -> ComplianceReport:
        framework = self.frameworks[framework_id]
        self.collector.collection_errors.clear()
        report = ComplianceReport(
            skill_name=skill_name,
            skill_version=skill_version,
            framework_id=framework_id,
            framework_name=framework.name,
            generated_at=datetime.now(timezone.utc).isoformat(),
            generated_by=generated_by,
            risk_classification=risk_classification,
        )

        for control in framework.all_controls:
            if risk_classification and not self._control_applies(control, risk_classification):
                report.assessments.append(
                    ControlAssessment(control=control, status=ComplianceStatus.NOT_APPLICABLE, evidence=[])
                )
                continue

            evidence = self.collector.collect_for_control(control, skill_name, skill_version, framework_id, time_range)
            evaluations = [self._evaluate_evidence(control, item) for item in evidence]
            status = self._evaluate(control, evidence, evaluations)
            report.assessments.append(
                ControlAssessment(
                    control=control,
                    status=status,
                    evidence=evidence,
                    evidence_evaluation=evaluations,
                    gap_description=self._gap(control, evidence, evaluations, status),
                    recommendation=self._recommend(control, status),
                )
            )
        return report

    def _control_applies(self, control: Control, rc: SkillRiskClassification) -> bool:
        # MINIMAL/LIMITED risk skills are not bound by high-risk controls that
        # require manual attestation; everything else applies.
        if rc.risk_level in (RiskLevel.MINIMAL,) and control.human_review_required:
            return False
        return True

    def _evaluate(
        self,
        control: Control,
        evidence: list[EvidenceRecord],
        evaluations: list[dict],
    ) -> ComplianceStatus:
        valid_types = {item.evidence_type for item, evaluation in zip(evidence, evaluations) if evaluation["valid"]}

        # Controls requiring human sign-off are PENDING until an attestation
        # exists and is identity-verified.
        if control.human_review_required:
            has_attestation = EvidenceType.MANUAL_ATTESTATION in valid_types
            if not has_attestation:
                return ComplianceStatus.PENDING_REVIEW

        if not evidence:
            return ComplianceStatus.NON_COMPLIANT

        required = set(control.evidence_types)
        coverage = len(required & valid_types) / len(required) if required else 1.0
        if coverage >= 1.0:
            return ComplianceStatus.COMPLIANT
        if coverage >= 0.5:
            return ComplianceStatus.PARTIALLY_COMPLIANT
        return ComplianceStatus.NON_COMPLIANT

    def _evaluate_evidence(self, control: Control, evidence: EvidenceRecord) -> dict:
        """Evaluate whether one evidence record actually supports its control."""
        expected_hash = hashlib.sha256(json.dumps(evidence.content, sort_keys=True, default=str).encode()).hexdigest()
        if evidence.integrity_hash != expected_hash:
            return {"evidence_type": evidence.evidence_type.value, "valid": False, "reason": "integrity hash mismatch"}

        content = evidence.content
        valid = False
        reason = "evidence does not satisfy the control"

        if evidence.evidence_type == EvidenceType.SECURITY_SCAN:
            valid = content.get("passed") is True and content.get("critical", 0) == 0
            reason = "scan passed with no critical findings" if valid else "scan failed or has critical findings"
        elif evidence.evidence_type == EvidenceType.AUDIT_LOG:
            integrity = content.get("integrity") or {}
            valid = (
                content.get("entry_count", 0) > 0
                and integrity.get("verified") is True
                and integrity.get("invalid", 0) == 0
                and integrity.get("parse_errors", 0) == 0
            )
            reason = "HMAC chain verified" if valid else "audit chain is absent or not cryptographically verified"
        elif evidence.evidence_type == EvidenceType.POLICY_EVALUATION:
            count = content.get("evaluation_count", 0)
            decisions = content.get("decisions") or {}
            valid = count > 0 and sum(decisions.values()) == count
            reason = "subject-scoped policy decisions recorded" if valid else "no complete policy decision evidence"
        elif evidence.evidence_type == EvidenceType.RBAC_ASSIGNMENT:
            valid = content.get("subject_scoped") is True and content.get("auth_decisions", 0) > 0
            reason = "subject-scoped authorization decisions recorded" if valid else "RBAC evidence is not skill-scoped"
        elif evidence.evidence_type == EvidenceType.SKILL_METADATA:
            required_fields = ["name", "version", "description"]
            query = control.evidence_query.lower()
            for field_name in ("data_sources", "accuracy_metrics", "allowed_tools"):
                if field_name in query:
                    required_fields.append(field_name)
            missing = [field_name for field_name in required_fields if not content.get(field_name)]
            valid = not missing
            reason = "required metadata is present" if valid else f"missing metadata: {', '.join(missing)}"
        elif evidence.evidence_type == EvidenceType.VERSION_HISTORY:
            valid = content.get("count", 0) > 0 and bool(content.get("versions"))
            reason = "version history is present" if valid else "no version history"
        elif evidence.evidence_type == EvidenceType.DEPLOYMENT_RECORD:
            valid = content.get("deployment_count", 0) > 0 and content.get("rollback_capable") is True
            reason = "deployment has a rollback target" if valid else "deployment is absent or cannot roll back"
        elif evidence.evidence_type == EvidenceType.MANUAL_ATTESTATION:
            valid = (
                bool(content.get("statement"))
                and bool(content.get("attested_by"))
                and content.get("identity_verified") is True
            )
            reason = "attester identity verified" if valid else "attestation identity is unverified"
        elif evidence.evidence_type == EvidenceType.OTEL_TRACE:
            valid = content.get("trace_count", 0) > 0 and content.get("integrity_verified") is True
            reason = "verified runtime traces present" if valid else "no verified runtime traces"

        return {"evidence_type": evidence.evidence_type.value, "valid": valid, "reason": reason}

    def _gap(
        self,
        control: Control,
        evidence: list[EvidenceRecord],
        evaluations: list[dict],
        status: ComplianceStatus,
    ) -> Optional[str]:
        if status in (ComplianceStatus.COMPLIANT, ComplianceStatus.NOT_APPLICABLE):
            return None
        valid_types = {item.evidence_type for item, evaluation in zip(evidence, evaluations) if evaluation["valid"]}
        missing = [t.value for t in control.evidence_types if t not in valid_types]
        if status == ComplianceStatus.PENDING_REVIEW:
            return "Requires a current, identity-verified human attestation"
        invalid_reasons = [evaluation["reason"] for evaluation in evaluations if not evaluation["valid"]]
        if missing:
            detail = f"Missing or invalid evidence: {', '.join(missing)}"
            if invalid_reasons:
                detail += f" ({'; '.join(sorted(set(invalid_reasons)))})"
            return detail
        return "No valid evidence collected"

    def _recommend(self, control: Control, status: ComplianceStatus) -> Optional[str]:
        if status == ComplianceStatus.PENDING_REVIEW:
            return (
                "Obtain an identity-verified attestation through an authenticated governance "
                f"integration for control {control.id}; local CLI attestations are non-enforcing"
            )
        if status == ComplianceStatus.NON_COMPLIANT:
            if EvidenceType.DEPLOYMENT_RECORD in control.evidence_types:
                return "Deploy via a progressive strategy (canary/blue-green) to record rollback capability"
            if EvidenceType.SECURITY_SCAN in control.evidence_types:
                return "Run skillctl eval audit and resolve findings"
            if EvidenceType.SKILL_METADATA in control.evidence_types:
                return "Complete the skill metadata (description, data_sources, accuracy_metrics)"
            return "Add the governance control that produces the required evidence"
        if status == ComplianceStatus.PARTIALLY_COMPLIANT:
            return "Supply the remaining evidence types to reach full compliance"
        return None

    # -- rendering -----------------------------------------------------------

    def to_json(self, report: ComplianceReport) -> dict:
        return {
            "report_type": "control_mapping_preview",
            "enforcement_ready": False,
            "collection_errors": list(self.collector.collection_errors),
            "skill_name": report.skill_name,
            "skill_version": report.skill_version,
            "framework_id": report.framework_id,
            "framework_name": report.framework_name,
            "generated_at": report.generated_at,
            "generated_by": report.generated_by,
            "risk_classification": report.risk_classification.to_dict() if report.risk_classification else None,
            "prohibited_use": bool(
                report.risk_classification and report.risk_classification.risk_level == RiskLevel.UNACCEPTABLE
            ),
            "compliance_score": round(report.compliance_score, 4),
            "summary": {
                "total": report.total_controls,
                "compliant": report.compliant_count,
                "partial": report.partial_count,
                "non_compliant": report.non_compliant_count,
                "pending": report.pending_count,
            },
            "assessments": [
                {
                    "control_id": a.control.id,
                    "control_name": a.control.name,
                    "status": a.status.value,
                    "gap": a.gap_description,
                    "recommendation": a.recommendation,
                    "evidence": [e.to_dict() for e in a.evidence],
                    "evidence_evaluation": a.evidence_evaluation,
                }
                for a in report.assessments
            ],
        }

    def to_markdown(self, report: ComplianceReport) -> str:
        lines = []
        lines.append(f"# Control Mapping Assessment (Preview): {report.skill_name}@{report.skill_version}")
        lines.append("")
        lines.append(
            "> This deterministic mapping is not a certification or enforcement gate. "
            "Independent control validation is still required."
        )
        if self.collector.collection_errors:
            lines.append(
                f"> Evidence collection reported {len(self.collector.collection_errors)} error(s); "
                "affected controls fail closed."
            )
        lines.append("")
        lines.append(f"- **Framework:** {report.framework_name} (`{report.framework_id}`)")
        if report.risk_classification:
            lines.append(f"- **Risk level:** {report.risk_classification.risk_level.value.upper()}")
            if report.risk_classification.risk_level == RiskLevel.UNACCEPTABLE:
                lines.append("- **Prohibited use:** yes — automated approval must not proceed")
        lines.append(f"- **Generated:** {report.generated_at} by {report.generated_by}")
        lines.append(
            f"- **Overall score:** {report.compliance_score * 100:.0f}% "
            f"({report.compliant_count}/{report.total_controls} compliant)"
        )
        lines.append("")

        def section(title: str, status: ComplianceStatus, icon: str):
            items = [a for a in report.assessments if a.status == status]
            if not items:
                return
            lines.append(f"## {icon} {title} ({len(items)})")
            for a in items:
                ev = ", ".join(sorted({e.evidence_type.value for e in a.evidence})) or "none"
                lines.append(f"- **{a.control.id}** — {a.control.name}  ")
                lines.append(f"  evidence: {ev}" + (f"; {a.gap_description}" if a.gap_description else ""))
            lines.append("")

        section("Compliant", ComplianceStatus.COMPLIANT, "✓")
        section("Partially compliant", ComplianceStatus.PARTIALLY_COMPLIANT, "⚠")
        section("Non-compliant", ComplianceStatus.NON_COMPLIANT, "✗")
        section("Pending attestation", ComplianceStatus.PENDING_REVIEW, "⏳")

        gaps = [a for a in report.assessments if a.recommendation]
        if gaps:
            lines.append("## Recommendations")
            for i, a in enumerate(gaps, 1):
                lines.append(f"{i}. **{a.control.id}**: {a.recommendation}")
            lines.append("")
        return "\n".join(lines)
