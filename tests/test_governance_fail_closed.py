"""Regression tests for fail-closed evaluation and control mapping."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from skillctl.compliance.frameworks import (
    Category,
    ComplianceFramework,
    ComplianceStatus,
    Control,
    EvidenceRecord,
    EvidenceType,
    Requirement,
    RiskLevel,
    SkillRiskClassification,
)
from skillctl.compliance.report import ComplianceReportGenerator
from skillctl.eval.unified_report import run_unified_report


class _StaticCollector:
    def __init__(self, records: list[EvidenceRecord]):
        self.records = records
        self.collection_errors: list[dict] = []

    def collect_for_control(self, *args, **kwargs):  # noqa: ARG002
        return self.records


def _record(evidence_type: EvidenceType, content: dict) -> EvidenceRecord:
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()
    return EvidenceRecord(
        control_id="control-1",
        evidence_type=evidence_type,
        collected_at="2026-01-01T00:00:00Z",
        source="test",
        content=content,
        valid_from="epoch",
        valid_until=None,
        integrity_hash=digest,
    )


def _generate(
    evidence_type: EvidenceType,
    content: dict,
    *,
    human_review_required: bool = False,
    risk: RiskLevel | None = None,
):
    control = Control(
        id="control-1",
        name="Test control",
        description="",
        evidence_types=[evidence_type],
        human_review_required=human_review_required,
    )
    framework = ComplianceFramework(
        id="test",
        name="Test framework",
        version="1",
        categories=[
            Category(id="cat", name="Category", requirements=[Requirement(id="req", name="Req", controls=[control])])
        ],
    )
    classification = None
    if risk is not None:
        classification = SkillRiskClassification(
            skill_name="org/skill",
            skill_version="1.0.0",
            risk_level=risk,
            classification_reason="test",
            classified_by="test",
            classified_at="2026-01-01T00:00:00Z",
        )
    generator = ComplianceReportGenerator(_StaticCollector([_record(evidence_type, content)]), {"test": framework})
    report = generator.generate(
        "org/skill",
        "1.0.0",
        "test",
        risk_classification=classification,
    )
    return generator, report


def test_unified_report_with_all_checks_skipped_fails(tmp_path: Path):
    output = tmp_path / "report.json"

    exit_code = run_unified_report(
        str(tmp_path),
        format="json",
        output_path=str(output),
        include_audit=False,
        include_contract=False,
    )

    report = json.loads(output.read_text())
    assert exit_code == 1
    assert report["passed"] is False
    assert report["overall_grade"] == "F"
    assert report["sections"]["audit"]["skipped"] is True
    assert report["sections"]["contract"]["skipped"] is True


def test_unified_report_component_error_fails(tmp_path: Path, monkeypatch):
    output = tmp_path / "report.json"

    def fail_audit(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("scanner unavailable")

    monkeypatch.setattr("skillctl.eval.unified_report.run_audit", fail_audit)
    exit_code = run_unified_report(
        str(tmp_path),
        output_path=str(output),
        include_audit=True,
        include_contract=False,
    )

    report = json.loads(output.read_text())
    assert exit_code == 1
    assert report["passed"] is False
    assert report["sections"]["audit"]["error"] == "scanner unavailable"


def test_failed_security_scan_is_non_compliant():
    _, report = _generate(
        EvidenceType.SECURITY_SCAN,
        {"passed": False, "grade": "F", "critical": 1},
    )

    assessment = report.assessments[0]
    assert assessment.status == ComplianceStatus.NON_COMPLIANT
    assert assessment.evidence_evaluation[0]["valid"] is False


def test_structural_audit_chain_is_not_treated_as_verified():
    _, report = _generate(
        EvidenceType.AUDIT_LOG,
        {"entry_count": 3, "integrity": {"chain_present": True}},
    )

    assert report.assessments[0].status == ComplianceStatus.NON_COMPLIANT


def test_deployment_without_rollback_target_is_non_compliant():
    _, report = _generate(
        EvidenceType.DEPLOYMENT_RECORD,
        {"deployment_count": 1, "rollback_capable": False},
    )

    assert report.assessments[0].status == ComplianceStatus.NON_COMPLIANT


def test_unverified_attestation_remains_pending_and_counts_as_gap():
    generator, report = _generate(
        EvidenceType.MANUAL_ATTESTATION,
        {
            "statement": "I approve",
            "attested_by": "arbitrary-user",
            "identity_verified": False,
        },
        human_review_required=True,
    )

    assert report.assessments[0].status == ComplianceStatus.PENDING_REVIEW
    assert report.compliance_score == 0.0
    assert report.gaps == report.assessments
    rendered = generator.to_json(report)
    assert rendered["report_type"] == "control_mapping_preview"
    assert rendered["enforcement_ready"] is False


def test_unacceptable_risk_forces_zero_score_and_prohibited_marker():
    generator, report = _generate(
        EvidenceType.SECURITY_SCAN,
        {"passed": True, "critical": 0},
        risk=RiskLevel.UNACCEPTABLE,
    )

    assert report.assessments[0].status == ComplianceStatus.COMPLIANT
    assert report.compliance_score == 0.0
    assert generator.to_json(report)["prohibited_use"] is True
