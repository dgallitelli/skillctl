"""Compliance report generator.

Maps SkillsOps governance controls to regulatory framework requirements and
produces audit-ready reports (JSON or Markdown). Synchronous and deterministic.
"""

from __future__ import annotations

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
    ComplianceStatus.PENDING_REVIEW: 0.5,
}


@dataclass
class ControlAssessment:
    control: Control
    status: ComplianceStatus
    evidence: list[EvidenceRecord]
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
        applicable = [a for a in self.assessments if a.status != ComplianceStatus.NOT_APPLICABLE]
        if not applicable:
            return 1.0
        total = sum(_STATUS_SCORE.get(a.status, 0.0) for a in applicable)
        return total / len(applicable)

    @property
    def gaps(self) -> list[ControlAssessment]:
        return [
            a
            for a in self.assessments
            if a.status in (ComplianceStatus.NON_COMPLIANT, ComplianceStatus.PARTIALLY_COMPLIANT)
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
            status = self._evaluate(control, evidence)
            report.assessments.append(
                ControlAssessment(
                    control=control,
                    status=status,
                    evidence=evidence,
                    gap_description=self._gap(control, evidence, status),
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

    def _evaluate(self, control: Control, evidence: list[EvidenceRecord]) -> ComplianceStatus:
        # Controls requiring human sign-off are PENDING until an attestation
        # exists — awaiting review is distinct from failing.
        if control.human_review_required:
            has_attestation = any(e.evidence_type == EvidenceType.MANUAL_ATTESTATION for e in evidence)
            if not has_attestation:
                return ComplianceStatus.PENDING_REVIEW

        if not evidence:
            return ComplianceStatus.NON_COMPLIANT

        required = set(control.evidence_types)
        found = {e.evidence_type for e in evidence}
        coverage = len(required & found) / len(required) if required else 1.0
        if coverage >= 1.0:
            return ComplianceStatus.COMPLIANT
        if coverage >= 0.5:
            return ComplianceStatus.PARTIALLY_COMPLIANT
        return ComplianceStatus.NON_COMPLIANT

    def _gap(self, control: Control, evidence: list[EvidenceRecord], status: ComplianceStatus) -> Optional[str]:
        if status in (ComplianceStatus.COMPLIANT, ComplianceStatus.NOT_APPLICABLE):
            return None
        found = {e.evidence_type for e in evidence}
        missing = [t.value for t in control.evidence_types if t not in found]
        if status == ComplianceStatus.PENDING_REVIEW:
            return "Requires human attestation"
        return f"Missing evidence: {', '.join(missing)}" if missing else "No evidence collected"

    def _recommend(self, control: Control, status: ComplianceStatus) -> Optional[str]:
        if status == ComplianceStatus.PENDING_REVIEW:
            return f"Run: skillctl compliance attest --control {control.id} --skill <skill>"
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
            "skill_name": report.skill_name,
            "skill_version": report.skill_version,
            "framework_id": report.framework_id,
            "framework_name": report.framework_name,
            "generated_at": report.generated_at,
            "generated_by": report.generated_by,
            "risk_classification": report.risk_classification.to_dict() if report.risk_classification else None,
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
                }
                for a in report.assessments
            ],
        }

    def to_markdown(self, report: ComplianceReport) -> str:
        lines = []
        lines.append(f"# Compliance Report: {report.skill_name}@{report.skill_version}")
        lines.append("")
        lines.append(f"- **Framework:** {report.framework_name} (`{report.framework_id}`)")
        if report.risk_classification:
            lines.append(f"- **Risk level:** {report.risk_classification.risk_level.value.upper()}")
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
