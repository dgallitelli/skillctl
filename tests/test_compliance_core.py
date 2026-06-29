"""Unit tests for the compliance core: frameworks, classification, attestations."""

from __future__ import annotations

import pytest

from skillctl.compliance.attestation import AttestationStore
from skillctl.compliance.classification import RiskClassifier
from skillctl.compliance.frameworks import EvidenceType, RiskLevel, load_builtin_frameworks


class TestFrameworks:
    def test_three_builtins_load(self):
        fws = load_builtin_frameworks()
        assert {"eu-ai-act", "iso-42001", "nist-ai-rmf"} <= set(fws)

    def test_eu_act_controls_have_evidence_types(self):
        fw = load_builtin_frameworks()["eu-ai-act"]
        assert fw.all_controls
        for c in fw.all_controls:
            assert all(isinstance(e, EvidenceType) for e in c.evidence_types)

    def test_some_controls_require_human_review(self):
        fw = load_builtin_frameworks()["eu-ai-act"]
        assert any(c.human_review_required for c in fw.all_controls)


class TestClassification:
    def test_unacceptable_beats_high(self):
        rc = RiskClassifier().classify({"name": "x", "description": "biometric facial_recognition", "version": "1"})
        assert rc.risk_level == RiskLevel.UNACCEPTABLE

    def test_public_interaction_is_limited(self):
        rc = RiskClassifier().classify(
            {"name": "x", "description": "chatbot", "version": "1"},
            deployment_context={"interacts_with_public": True},
        )
        assert rc.risk_level == RiskLevel.LIMITED

    def test_human_attestation_overrides(self):
        rc = RiskClassifier().classify(
            {"name": "x", "description": "hiring", "version": "1"},
            human_attestation={"risk_level": "minimal", "reason": "internal demo only", "by": "admin"},
        )
        assert rc.risk_level == RiskLevel.MINIMAL

    def test_interactive_questionnaire(self):
        qs = RiskClassifier().classify_interactive({"name": "x"})
        assert any(q["key"] == "uses_biometric_data" for q in qs)


class TestAttestations:
    @pytest.fixture
    def store(self):
        s = AttestationStore(":memory:")
        s.initialize()
        yield s
        s.close()

    def test_add_and_get_active(self, store):
        store.add(
            control_id="art-9-2-b",
            skill_name="proj/x",
            skill_version="1.0.0",
            framework_id="eu-ai-act",
            attested_by="bob",
            statement="ok",
        )
        active = store.get_active("art-9-2-b", "proj/x", "1.0.0")
        assert active is not None and active.attested_by == "bob"

    def test_supersession(self, store):
        store.add(control_id="c", skill_name="s", skill_version="1", framework_id="f", attested_by="a", statement="v1")
        store.add(control_id="c", skill_name="s", skill_version="1", framework_id="f", attested_by="a", statement="v2")
        active = store.get_active("c", "s", "1")
        assert active.statement == "v2"  # latest wins, old superseded

    def test_expired_not_active(self, store):
        store.add(
            control_id="c",
            skill_name="s",
            skill_version="1",
            framework_id="f",
            attested_by="a",
            statement="x",
            expiry_days=-1,  # already expired
        )
        assert store.get_active("c", "s", "1") is None

    def test_version_specific(self, store):
        store.add(
            control_id="c", skill_name="s", skill_version="1.0.0", framework_id="f", attested_by="a", statement="x"
        )
        assert store.get_active("c", "s", "1.0.0") is not None
        assert store.get_active("c", "s", "2.0.0") is None  # new version invalidates
