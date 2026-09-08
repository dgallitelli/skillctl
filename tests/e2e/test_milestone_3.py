"""End-to-end tests for Milestone 3: Compliance & Progressive Deployment.

Real compliance frameworks, real evidence collection (security scan, HMAC audit
log, attestations), real deployment engine + health monitor + traffic router.
No mocks. No monkeypatching.

Run with:  pytest tests/e2e/test_milestone_3.py -v
"""

from __future__ import annotations

import re
import time

import pytest

from skillctl.compliance.attestation import AttestationStore
from skillctl.compliance.classification import RiskClassifier
from skillctl.compliance.evidence import EvidenceCollector
from skillctl.compliance.frameworks import ComplianceStatus, RiskLevel, load_builtin_frameworks
from skillctl.compliance.report import ComplianceReportGenerator
from skillctl.deployment.engine import DeploymentEngine
from skillctl.deployment.health import HealthMonitor
from skillctl.deployment.models import DeploymentState, DeploymentStrategy
from skillctl.deployment.router import TrafficRouter
from skillctl.deployment.store import DeploymentStore
from skillctl.registry.audit import AuditLogger

pytestmark = pytest.mark.integration

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _write_skill(directory, name="proj/demo", version="1.0.0", description="A demo skill for compliance tests."):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "skill.yaml").write_text(
        "apiVersion: skillctl.io/v1\nkind: Skill\nmetadata:\n"
        f"  name: {name}\n  version: {version}\n"
        f'  description: "{description}"\n'
        "spec:\n  content:\n    path: SKILL.md\n  capabilities:\n    - read_file\n"
    )
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# Demo\n\nDoes a thing safely.\n"
    )
    return directory


# ═══════════════════════════════════════════════════════════════════
# Part A — Compliance
# ═══════════════════════════════════════════════════════════════════


def test_e2e_risk_classification():
    rc = RiskClassifier()
    hiring = rc.classify(
        {"name": "hr/screener", "description": "Automated hiring and recruitment screening", "version": "1.0.0"}
    )
    assert hiring.risk_level == RiskLevel.HIGH

    social = rc.classify({"name": "gov/score", "description": "citizen social_scoring system", "version": "1.0.0"})
    assert social.risk_level == RiskLevel.UNACCEPTABLE

    generic = rc.classify({"name": "util/echo", "description": "echoes text back", "version": "1.0.0"})
    assert generic.risk_level == RiskLevel.MINIMAL


def test_e2e_compliance_report_generation(tmp_path):
    skill_dir = _write_skill(tmp_path / "skill")

    # Real HMAC audit log with entries for this skill.
    audit = AuditLogger(tmp_path / "audit.jsonl", hmac_key=b"m3-key")
    audit.log(action="skill.created", actor="alice", resource="proj/demo@1.0.0", details={})
    audit.log(
        action="policy_decision",
        actor="alice",
        resource="proj/demo@1.0.0",
        details={"hook_name": "data-boundary", "decision": "allow"},
    )

    collector = EvidenceCollector(
        skill_path=str(skill_dir),
        audit_log_path=str(tmp_path / "audit.jsonl"),
        audit_hmac_key=b"m3-key",
    )
    fws = load_builtin_frameworks()
    gen = ComplianceReportGenerator(collector, fws)
    report = gen.generate("proj/demo", "1.0.0", "eu-ai-act")

    by_id = {a.control.id: a for a in report.assessments}

    # Tamper-evident logging control — audit evidence present → COMPLIANT.
    assert by_id["art-12-1-a"].status == ComplianceStatus.COMPLIANT
    # Security scanning control — live scan ran → COMPLIANT.
    assert by_id["art-15-4-a"].status == ComplianceStatus.COMPLIANT
    # Residual risk acceptability — manual only, no attestation → PENDING_REVIEW.
    assert by_id["art-9-2-b"].status == ComplianceStatus.PENDING_REVIEW
    # Intervention mechanism — policy+deployment, no deployment record → not compliant.
    assert by_id["art-14-1-b"].status in (ComplianceStatus.NON_COMPLIANT, ComplianceStatus.PARTIALLY_COMPLIANT)

    # Evidence integrity hashes are real SHA-256.
    for a in report.assessments:
        for e in a.evidence:
            assert _SHA256.match(e.integrity_hash)

    assert 0.0 <= report.compliance_score <= 1.0
    assert report.total_controls == len(fws["eu-ai-act"].all_controls)

    # JSON + Markdown rendering work.
    assert gen.to_json(report)["framework_id"] == "eu-ai-act"
    assert "Control Mapping Assessment (Preview)" in gen.to_markdown(report)


def test_e2e_manual_attestation_workflow(tmp_path):
    skill_dir = _write_skill(tmp_path / "skill")
    attest = AttestationStore(tmp_path / "att.db")
    attest.initialize()

    collector = EvidenceCollector(skill_path=str(skill_dir), attestation_store=attest)
    fws = load_builtin_frameworks()
    gen = ComplianceReportGenerator(collector, fws)

    # Before attestation: art-9-2-b is pending.
    report1 = gen.generate("proj/demo", "1.0.0", "eu-ai-act")
    a1 = {a.control.id: a for a in report1.assessments}["art-9-2-b"]
    assert a1.status == ComplianceStatus.PENDING_REVIEW

    # Submit attestation.
    attest.add(
        control_id="art-9-2-b",
        skill_name="proj/demo",
        skill_version="1.0.0",
        framework_id="eu-ai-act",
        attested_by="publisher-bob",
        statement="Residual risks reviewed and accepted per risk assessment v3",
        identity_verified=True,
    )

    # After attestation: art-9-2-b is compliant (manual evidence present).
    report2 = gen.generate("proj/demo", "1.0.0", "eu-ai-act")
    a2 = {a.control.id: a for a in report2.assessments}["art-9-2-b"]
    assert a2.status == ComplianceStatus.COMPLIANT
    assert report2.compliance_score > report1.compliance_score


# ═══════════════════════════════════════════════════════════════════
# Part B — Progressive Deployment
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def deploy(tmp_path):
    store = DeploymentStore(tmp_path / "dep.db")
    store.initialize()
    engine = DeploymentEngine(store)
    yield engine, store
    store.close()


def test_e2e_canary_deployment(deploy):
    engine, store = deploy
    engine.create(
        skill_name="proj/x",
        skill_namespace="org/acme",
        to_version="2.0.0",
        from_version="1.0.0",
        strategy=DeploymentStrategy.CANARY,
        config={"stages": [0.10, 0.50, 1.0]},
    )
    router = TrafficRouter(store)
    new = sum(1 for i in range(100) if router.resolve_version("proj/x", "org/acme", f"user-{i}", "1.0.0") == "2.0.0")
    # ~10% with tolerance for hash distribution over 100 actors.
    assert 3 <= new <= 20, f"expected ~10, got {new}"

    # Consistent routing: same actor always gets the same version.
    v1 = router.resolve_version("proj/x", "org/acme", "user-42", "1.0.0")
    v2 = router.resolve_version("proj/x", "org/acme", "user-42", "1.0.0")
    assert v1 == v2


def test_e2e_canary_promote_to_full(deploy):
    engine, store = deploy
    dep = engine.create(
        skill_name="proj/y",
        skill_namespace="org/acme",
        to_version="2.0.0",
        from_version="1.0.0",
        strategy=DeploymentStrategy.CANARY,
        config={"stages": [0.10, 1.0]},
    )
    dep = engine.promote(dep.id, approved_by="admin")
    assert dep.current_traffic_percent == 1.0
    assert dep.state == DeploymentState.COMPLETED
    # At 100%, everyone gets the new version.
    router = TrafficRouter(store)
    assert router.resolve_version("proj/y", "org/acme", "anyone", "1.0.0") == "2.0.0"


def test_e2e_rollback_on_failure(deploy):
    engine, store = deploy
    dep = engine.create(
        skill_name="proj/z",
        skill_namespace="org/acme",
        to_version="2.0.0",
        from_version="1.0.0",
        strategy=DeploymentStrategy.CANARY,
        config={
            "stages": [0.10, 1.0],
            "auto_rollback": True,
            "health_threshold": {"min_sample_size": 10, "max_error_rate": 0.05},
        },
    )
    now = int(time.time())
    # Inject 20 invocations of v2 with a 30% error rate (breaches 5% threshold).
    for i in range(20):
        store.record_metric(dep.id, "2.0.0", now, success=i >= 6, error=i < 6, latency_ms=100)

    monitor = HealthMonitor(store)
    health = engine.evaluate_and_maybe_rollback(dep.id, monitor, now=now + 1)
    assert health["healthy"] is False
    assert health["rolled_back"] is True

    rolled = store.get(dep.id)
    assert rolled.state == DeploymentState.ROLLED_BACK
    # Traffic now serves the previous version.
    router = TrafficRouter(store)
    assert router.resolve_version("proj/z", "org/acme", "user-1", "1.0.0") == "1.0.0"


def test_e2e_rollback_not_triggered_when_healthy(deploy):
    engine, store = deploy
    dep = engine.create(
        skill_name="proj/healthy",
        skill_namespace="org/acme",
        to_version="2.0.0",
        from_version="1.0.0",
        strategy=DeploymentStrategy.CANARY,
        config={"stages": [0.10, 1.0], "auto_rollback": True, "health_threshold": {"min_sample_size": 10}},
    )
    now = int(time.time())
    for _ in range(20):
        store.record_metric(dep.id, "2.0.0", now, success=True, latency_ms=50)
    monitor = HealthMonitor(store)
    health = engine.evaluate_and_maybe_rollback(dep.id, monitor, now=now + 1)
    assert health["healthy"] is True and health["rolled_back"] is False


def test_e2e_blue_green_switch(deploy):
    engine, store = deploy
    dep = engine.create(
        skill_name="proj/bg",
        skill_namespace="org/acme",
        to_version="2.0.0",
        from_version="1.0.0",
        strategy=DeploymentStrategy.BLUE_GREEN,
    )
    router = TrafficRouter(store)
    # Before switch: blue (old version) serves.
    assert router.resolve_version("proj/bg", "org/acme", "user-1", "1.0.0") == "1.0.0"

    # Promote = switch traffic to green (new version).
    dep = engine.promote(dep.id, approved_by="admin")
    assert dep.state == DeploymentState.COMPLETED
    assert router.resolve_version("proj/bg", "org/acme", "user-1", "1.0.0") == "2.0.0"
