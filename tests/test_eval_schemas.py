"""Tests for the audit eval dataclass schemas.

Milestone 0 removed the functional/trigger/compare schemas
(skillctl.eval.eval_schemas) along with the LLM-as-judge evaluators.
What remains here covers the deterministic audit schema in
skillctl.eval.schemas: Finding citation round-trips and apply_config
propagation.
"""

from skillctl.eval.schemas import Category, Finding, Severity


# ---------------------------------------------------------------------------
# Finding citation field tests
# ---------------------------------------------------------------------------


def test_finding_carries_citation_in_to_dict():
    f = Finding(
        code="QLT-001",
        severity=Severity.INFO,
        category=Category.QUALITY,
        title="t",
        detail="d",
        citation="Agent Skills spec §required-fields",
    )
    d = f.to_dict()
    assert d["citation"] == "Agent Skills spec §required-fields"


def test_finding_citation_defaults_to_none():
    f = Finding(
        code="STR-001",
        severity=Severity.CRITICAL,
        category=Category.STRUCTURE,
        title="t",
        detail="d",
    )
    assert f.citation is None
    assert f.to_dict()["citation"] is None


def test_apply_config_preserves_citation_through_severity_override():
    """apply_config must not drop citation when it rebuilds Finding for a severity override."""
    from skillctl.eval.config import AuditConfig, apply_config

    f = Finding(
        code="SEC-002",
        severity=Severity.CRITICAL,
        category=Category.SECURITY,
        title="External URL",
        detail="Skill references an external URL",
        citation="platform.claude.com agent-skills/security §external-urls",
    )
    config = AuditConfig(severity_overrides={"SEC-002": "WARNING"})

    result = apply_config([f], config)

    assert len(result) == 1
    assert result[0].severity == Severity.WARNING  # override applied
    assert result[0].citation == "platform.claude.com agent-skills/security §external-urls"
