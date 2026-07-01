"""Compliance framework definitions.

Hierarchy: Framework → Category → Requirement → Control. Controls map to
evidence sources within SkillsOps so evidence can be collected automatically.
Frameworks are declared in YAML (``frameworks_data/*.yaml``) and loaded lazily.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

_DATA_DIR = Path(__file__).parent / "frameworks_data"


class RiskLevel(Enum):
    """EU AI Act risk classification levels."""

    UNACCEPTABLE = "unacceptable"
    HIGH = "high"
    LIMITED = "limited"
    MINIMAL = "minimal"


class ComplianceStatus(Enum):
    """Status of a control's compliance."""

    COMPLIANT = "compliant"
    PARTIALLY_COMPLIANT = "partial"
    NON_COMPLIANT = "non_compliant"
    NOT_APPLICABLE = "n/a"
    PENDING_REVIEW = "pending"


class EvidenceType(Enum):
    """Types of evidence collectable from SkillsOps."""

    AUDIT_LOG = "audit_log"
    SECURITY_SCAN = "security_scan"
    RBAC_ASSIGNMENT = "rbac"
    POLICY_EVALUATION = "policy"
    OTEL_TRACE = "otel_trace"
    SKILL_METADATA = "metadata"
    VERSION_HISTORY = "version"
    DEPLOYMENT_RECORD = "deployment"
    MANUAL_ATTESTATION = "manual"


@dataclass
class Control:
    """A specific governance control that can be verified."""

    id: str
    name: str
    description: str
    evidence_types: list[EvidenceType]
    evidence_query: str = ""
    automated: bool = True
    human_review_required: bool = False


@dataclass
class Requirement:
    id: str
    name: str
    description: str = ""
    controls: list[Control] = field(default_factory=list)


@dataclass
class Category:
    id: str
    name: str
    requirements: list[Requirement] = field(default_factory=list)


@dataclass
class ComplianceFramework:
    id: str
    name: str
    version: str
    effective_date: str = ""
    jurisdiction: str = ""
    categories: list[Category] = field(default_factory=list)

    @property
    def all_controls(self) -> list[Control]:
        controls: list[Control] = []
        for cat in self.categories:
            for req in cat.requirements:
                controls.extend(req.controls)
        return controls


@dataclass
class SkillRiskClassification:
    skill_name: str
    skill_version: str
    risk_level: RiskLevel
    classification_reason: str
    classified_by: str
    classified_at: str
    applicable_frameworks: list[str] = field(default_factory=list)
    uses_biometric_data: bool = False
    makes_decisions_affecting_people: bool = False
    operates_critical_infrastructure: bool = False
    used_in_employment_context: bool = False
    used_in_education_context: bool = False
    interacts_with_public: bool = False

    def to_dict(self) -> dict:
        return {
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "risk_level": self.risk_level.value,
            "classification_reason": self.classification_reason,
            "classified_by": self.classified_by,
            "classified_at": self.classified_at,
            "applicable_frameworks": self.applicable_frameworks,
        }


@dataclass
class EvidenceRecord:
    control_id: str
    evidence_type: EvidenceType
    collected_at: str
    source: str
    content: dict
    valid_from: str
    valid_until: Optional[str]
    integrity_hash: str

    def to_dict(self) -> dict:
        return {
            "control_id": self.control_id,
            "evidence_type": self.evidence_type.value,
            "collected_at": self.collected_at,
            "source": self.source,
            "content": self.content,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "integrity_hash": self.integrity_hash,
        }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _parse_framework(raw: dict) -> ComplianceFramework:
    categories = []
    for cat in raw.get("categories", []) or []:
        requirements = []
        for req in cat.get("requirements", []) or []:
            controls = []
            for ctl in req.get("controls", []) or []:
                controls.append(
                    Control(
                        id=ctl["id"],
                        name=ctl["name"],
                        description=ctl.get("description", ""),
                        evidence_types=[EvidenceType(e) for e in ctl.get("evidence_types", [])],
                        evidence_query=ctl.get("evidence_query", ""),
                        automated=ctl.get("automated", True),
                        human_review_required=ctl.get("human_review_required", False),
                    )
                )
            requirements.append(
                Requirement(id=req["id"], name=req["name"], description=req.get("description", ""), controls=controls)
            )
        categories.append(Category(id=cat["id"], name=cat["name"], requirements=requirements))
    return ComplianceFramework(
        id=raw["id"],
        name=raw["name"],
        version=str(raw.get("version", "")),
        effective_date=str(raw.get("effective_date", "")),
        jurisdiction=raw.get("jurisdiction", ""),
        categories=categories,
    )


def load_framework(path: str | Path) -> ComplianceFramework:
    raw = yaml.safe_load(Path(path).read_text())
    return _parse_framework(raw)


def load_builtin_frameworks() -> dict[str, ComplianceFramework]:
    """Load all framework YAML files shipped with the package."""
    frameworks: dict[str, ComplianceFramework] = {}
    for yaml_file in sorted(_DATA_DIR.glob("*.yaml")):
        fw = load_framework(yaml_file)
        frameworks[fw.id] = fw
    return frameworks
