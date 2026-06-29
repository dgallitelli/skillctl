"""Risk classification for AI skills (EU AI Act Annex III, simplified).

Determines which compliance obligations apply. Classification priority:
1. Human attestation (overrides automated),
2. Keyword analysis of skill metadata,
3. Deployment context,
4. Default: MINIMAL.
"""

from __future__ import annotations

from datetime import datetime, timezone

from skillctl.compliance.frameworks import RiskLevel, SkillRiskClassification

# Keyword → risk level. Order matters only for reporting the matched reason;
# UNACCEPTABLE always wins over HIGH.
HIGH_RISK_INDICATORS: dict[str, RiskLevel] = {
    "facial_recognition": RiskLevel.UNACCEPTABLE,
    "facial recognition": RiskLevel.UNACCEPTABLE,
    "social_scoring": RiskLevel.UNACCEPTABLE,
    "social scoring": RiskLevel.UNACCEPTABLE,
    "biometric": RiskLevel.HIGH,
    "hiring": RiskLevel.HIGH,
    "recruitment": RiskLevel.HIGH,
    "employment": RiskLevel.HIGH,
    "credit_scoring": RiskLevel.HIGH,
    "credit scoring": RiskLevel.HIGH,
    "medical_diagnosis": RiskLevel.HIGH,
    "medical diagnosis": RiskLevel.HIGH,
    "critical_infrastructure": RiskLevel.HIGH,
    "critical infrastructure": RiskLevel.HIGH,
    "law_enforcement": RiskLevel.HIGH,
    "law enforcement": RiskLevel.HIGH,
    "education_assessment": RiskLevel.HIGH,
    "exam grading": RiskLevel.HIGH,
}

_RANK = {RiskLevel.MINIMAL: 0, RiskLevel.LIMITED: 1, RiskLevel.HIGH: 2, RiskLevel.UNACCEPTABLE: 3}


class RiskClassifier:
    """Classifies skills according to EU AI Act risk levels."""

    def classify(
        self,
        skill_metadata: dict,
        deployment_context: dict | None = None,
        human_attestation: dict | None = None,
        classified_by: str = "system",
    ) -> SkillRiskClassification:
        name = skill_metadata.get("name", "")
        version = str(skill_metadata.get("version", ""))
        now = datetime.now(timezone.utc).isoformat()

        # 1. Human attestation overrides everything.
        if human_attestation and "risk_level" in human_attestation:
            level = RiskLevel(human_attestation["risk_level"])
            return SkillRiskClassification(
                skill_name=name,
                skill_version=version,
                risk_level=level,
                classification_reason=human_attestation.get("reason", "Human attestation"),
                classified_by=human_attestation.get("by", classified_by),
                classified_at=now,
                applicable_frameworks=["eu-ai-act", "iso-42001", "nist-ai-rmf"],
            )

        # 2. Keyword analysis over name + description + category + tags.
        haystack = " ".join(
            [
                str(skill_metadata.get("name", "")),
                str(skill_metadata.get("description", "")),
                str(skill_metadata.get("category", "")),
                " ".join(skill_metadata.get("tags", []) or []),
            ]
        ).lower()

        best_level = RiskLevel.MINIMAL
        matched: list[str] = []
        for keyword, level in HIGH_RISK_INDICATORS.items():
            if keyword in haystack:
                matched.append(keyword)
                if _RANK[level] > _RANK[best_level]:
                    best_level = level

        if best_level != RiskLevel.MINIMAL:
            reason = f"Matched high-risk indicator(s): {', '.join(sorted(set(matched)))}"
        else:
            # 3. Deployment context can bump to LIMITED (e.g. public interaction).
            ctx = deployment_context or {}
            if ctx.get("interacts_with_public"):
                best_level = RiskLevel.LIMITED
                reason = "Interacts with the public — transparency obligations apply"
            else:
                reason = "No high-risk indicators found"

        return SkillRiskClassification(
            skill_name=name,
            skill_version=version,
            risk_level=best_level,
            classification_reason=reason,
            classified_by=classified_by,
            classified_at=now,
            applicable_frameworks=["eu-ai-act", "iso-42001", "nist-ai-rmf"],
            used_in_employment_context=any(k in haystack for k in ("hiring", "recruitment", "employment")),
            uses_biometric_data="biometric" in haystack or "facial" in haystack,
            interacts_with_public=bool((deployment_context or {}).get("interacts_with_public")),
        )

    def classify_interactive(self, skill_metadata: dict) -> list[dict]:
        """Return a questionnaire for ambiguous cases (used by the CLI)."""
        return [
            {"key": "uses_biometric_data", "question": "Does this skill process biometric data?"},
            {
                "key": "makes_decisions_affecting_people",
                "question": "Does it make decisions affecting people (hiring, credit, benefits)?",
            },
            {
                "key": "operates_critical_infrastructure",
                "question": "Does it operate or manage critical infrastructure?",
            },
            {"key": "used_in_education_context", "question": "Is it used to assess students or learners?"},
            {"key": "interacts_with_public", "question": "Does it interact directly with the public?"},
        ]
