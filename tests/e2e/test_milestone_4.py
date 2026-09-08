"""End-to-end tests for Milestone 4: Enterprise Scale & Ecosystem.

Real HS256 JWTs, real ABAC evaluation through the runtime interceptor, real
SQLite lineage, and real in-process RBAC registries for federation.
No mocks. No monkeypatching.

Run with:  pytest tests/e2e/test_milestone_4.py -v
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillctl.abac.engine import ABACEngine
from skillctl.abac.hook import ABACPolicyHook
from skillctl.abac.models import ABACPolicy, Condition
from skillctl.federation.promote import promote_skill
from skillctl.forensics.query import ForensicQuery
from skillctl.identity import jwt as jwtlib
from skillctl.identity.models import GroupRoleMapping, IdentityProviderConfig, IdentityProviderType
from skillctl.identity.resolver import IdentityResolver, to_rbac_identity
from skillctl.lineage.store import LineageStore
from skillctl.policy.engine import PolicyEngine
from skillctl.policy.hooks import PolicyContext
from skillctl.policy.interceptor import PolicyViolation, SkillInterceptor
from skillctl.registry.api import api_router
from skillctl.registry.audit import AuditLogger
from skillctl.registry.auth import AuthManager
from skillctl.registry.db import MetadataDB
from skillctl.registry.rbac.engine import RBACEngine
from skillctl.registry.rbac.models import Permission
from skillctl.registry.rbac.store import RBACStore
from skillctl.registry.storage import FilesystemBackend

pytestmark = pytest.mark.integration


def run(coro):
    return asyncio.run(coro)


async def echo(**kwargs):
    return kwargs or {"ok": True}


# ═══════════════════════════════════════════════════════════════════
# Test 1 — OIDC login maps to the correct RBAC role
# ═══════════════════════════════════════════════════════════════════


def test_e2e_oidc_login_flow():
    secret = "federation-secret"
    cfg = IdentityProviderConfig(
        id="okta-prod",
        type=IdentityProviderType.OIDC,
        oidc_issuer_url="https://okta.example.com",
        oidc_audience="skillsops",
        group_mappings=[
            GroupRoleMapping("ml-publishers", "publisher", "org/acme/ml"),
            GroupRoleMapping("everyone", "viewer", "*"),
        ],
    )
    resolver = IdentityResolver(cfg, secret=secret)

    # A publisher logs in via the IdP.
    token = jwtlib.encode(
        {
            "sub": "alice",
            "email": "alice@acme.com",
            "groups": ["ml-publishers", "everyone"],
            "aud": "skillsops",
            "iss": "https://okta.example.com",
            "exp": int(time.time()) + 3600,
        },
        secret,
    )
    identity = resolver.resolve(token)
    assert "publisher:org/acme/ml" in identity.resolved_roles

    # The federated identity flows through the RBAC engine (inline roles drive it).
    _db = MetadataDB(":memory:")
    _db.initialize()
    _store = RBACStore(_db.conn)
    _store.initialize()
    engine = RBACEngine(_store)
    rbac_identity = to_rbac_identity(identity)
    # publisher@org/acme/ml covers the child namespace.
    assert engine.check(rbac_identity, Permission.SKILL_PUBLISH, "org/acme/ml/forecaster").allowed

    # A viewer-only user cannot publish.
    vtoken = jwtlib.encode(
        {"sub": "vic", "groups": ["everyone"], "aud": "skillsops", "iss": "https://okta.example.com"}, secret
    )
    viewer = to_rbac_identity(resolver.resolve(vtoken))
    assert not engine.check(viewer, Permission.SKILL_PUBLISH, "org/acme/ml/forecaster").allowed
    assert engine.check(viewer, Permission.SKILL_READ, "org/acme/ml/forecaster").allowed


# ═══════════════════════════════════════════════════════════════════
# Test 2 — ABAC time restriction (publish only during business hours)
# ═══════════════════════════════════════════════════════════════════


def test_e2e_abac_time_restriction():
    # Deny publish before 09:00 or at/after 17:00 (two deny policies = OR).
    engine = ABACEngine(
        policies=[
            ABACPolicy(
                "biz-early", "deny", [Condition("action", "eq", "publish"), Condition("environment.hour", "lt", 9)]
            ),
            ABACPolicy(
                "biz-late", "deny", [Condition("action", "eq", "publish"), Condition("environment.hour", "gte", 17)]
            ),
        ],
        default_allow=True,
    )
    policy_engine = PolicyEngine()
    policy_engine.register(ABACPolicyHook(engine))
    interceptor = SkillInterceptor(policy_engine=policy_engine)

    def ctx(ts):
        return PolicyContext(
            actor_id="alice",
            skill_name="org/x",
            skill_version="1.0.0",
            skill_namespace="org/acme",
            timestamp=ts,
            attributes={"action": "publish"},
        )

    # 14:00 → allowed.
    assert run(interceptor.invoke(echo, ctx("2026-07-15T14:00:00+00:00"), {"x": 1}))["x"] == 1

    # 02:00 → denied.
    with pytest.raises(PolicyViolation) as exc:
        run(interceptor.invoke(echo, ctx("2026-07-15T02:00:00+00:00"), {"x": 1}))
    assert exc.value.result.hook_name == "abac"
    assert "biz-early" in exc.value.result.details["matched_policy"]


# ═══════════════════════════════════════════════════════════════════
# Test 3 — Data lineage trace
# ═══════════════════════════════════════════════════════════════════


def test_e2e_data_lineage_trace(tmp_path):
    store = LineageStore(tmp_path / "lineage.db")
    store.initialize()

    # extractor reads source-A, writes intermediate-B
    store.record_access(
        invocation_id="inv-1",
        skill="org/extract",
        actor="alice",
        reads=["db:customers"],
        writes=["s3:features"],
        ts=100,
    )
    # model reads intermediate-B, writes output-C
    store.record_access(
        invocation_id="inv-2", skill="org/model", actor="bob", reads=["s3:features"], writes=["s3:predictions"], ts=200
    )

    q = ForensicQuery(store)
    # Full provenance of predictions traces back to the raw source.
    prov = q.provenance("s3:predictions")
    assert set(prov["sources"]) == {"s3:features", "db:customers"}
    # Downstream of the raw source includes the features it produced.
    downstream_refs = {d["data_ref"] for d in q.downstream("db:customers")}
    assert "s3:features" in downstream_refs
    store.close()


# ═══════════════════════════════════════════════════════════════════
# Test 4 — Incident forensics
# ═══════════════════════════════════════════════════════════════════


def test_e2e_incident_forensics(tmp_path):
    store = LineageStore(tmp_path / "lineage.db")
    store.initialize()
    base = 1_000_000

    # 50 invocations; the ones in [base+10, base+19] touch PII for skill "org/risky".
    for i in range(50):
        label = "pii" if 10 <= i < 20 else "public"
        skill = "org/risky" if i % 2 == 0 else "org/safe"
        store.record_access(
            invocation_id=f"inv-{i}",
            skill=skill,
            actor=f"user-{i % 3}",
            reads=[{"ref": f"data:item-{i}", "label": label}],
            ts=base + i,
        )

    q = ForensicQuery(store)
    # "Which invocations of org/risky accessed PII between T1 and T2?"
    hits = q.invocations_accessing(skill="org/risky", label="pii", since=base + 10, until=base + 19)
    # PII window is i in [10,19]; org/risky is even i → 10,12,14,16,18 = 5.
    assert len(hits) == 5
    assert all(h["skill"] == "org/risky" for h in hits)
    store.close()


# ═══════════════════════════════════════════════════════════════════
# Test 5 — Multi-registry promotion (dev → staging → prod)
# ═══════════════════════════════════════════════════════════════════


def _rbac_registry(tmp_path, label):
    data_dir = tmp_path / label
    data_dir.mkdir()
    app = FastAPI()
    app.include_router(api_router)
    db = MetadataDB(data_dir / "registry.db", check_same_thread=False)
    db.initialize()
    app.state.db = db
    app.state.storage = FilesystemBackend(data_dir)
    app.state.audit = AuditLogger(data_dir / "audit.jsonl", hmac_key=b"m4-" + label.encode())
    app.state.auth_manager = AuthManager(db, disabled=False)
    rbac_store = RBACStore(db.conn)
    rbac_store.initialize()
    app.state.rbac_store = rbac_store
    app.state.rbac_engine = RBACEngine(rbac_store)
    admin = rbac_store.bootstrap_admin()
    return app, TestClient(app), admin, rbac_store


def _client(app, token):
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


def _login(app, store, username, password, role, namespace):
    from skillctl.registry.rbac.models import role_from_str

    store.create_user(username, password)
    uid = store.get_user_by_username(username)["user_id"]
    store.add_assignment(uid, role_from_str(role), namespace, assigned_by="admin")
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": password})
    return r.json()["token"]


def _publish_skill(client, name, namespace, version="1.0.0"):
    import json as _json

    manifest = {
        "apiVersion": "skillctl.io/v1",
        "kind": "Skill",
        "metadata": {"name": name, "version": version, "description": "federated skill"},
        "spec": {"content": {"inline": "x"}},
    }
    create = client.post(
        "/api/v1/skills",
        data={"manifest": _json.dumps(manifest), "namespace": namespace},
        files={"content": ("SKILL.md", b"# Skill\n\nbody\n", "application/octet-stream")},
    )
    assert create.status_code == 201, create.text
    pub = client.post("/api/v1/skills/publish", json={"name": name, "version": version, "namespace": namespace})
    assert pub.status_code == 200, pub.text


def test_e2e_multi_registry_promotion(tmp_path):
    dev_app, dev_client, dev_admin, dev_store = _rbac_registry(tmp_path, "dev")
    stg_app, _, stg_admin, stg_store = _rbac_registry(tmp_path, "staging")
    prod_app, _, prod_admin, prod_store = _rbac_registry(tmp_path, "prod")

    dev_admin_client = _client(dev_app, dev_admin["token"])
    _publish_skill(dev_admin_client, "demo/tool", "org/acme")

    # Promote dev → staging as a PUBLISHER (allowed).
    pub_token = _login(stg_app, stg_store, "release-bot", "pw", "publisher", "org/acme")
    res = promote_skill(
        source_client=dev_admin_client,
        target_client=_client(stg_app, pub_token),
        name="demo/tool",
        version="1.0.0",
        target_namespace="org/acme",
    )
    assert res.promoted, res.reason

    # Promote staging → prod as a mere AUTHOR (publish denied by RBAC).
    author_token = _login(prod_app, prod_store, "release-bot", "pw", "author", "org/acme")
    stg_admin_client = _client(stg_app, stg_admin["token"])
    denied = promote_skill(
        source_client=stg_admin_client,
        target_client=_client(prod_app, author_token),
        name="demo/tool",
        version="1.0.0",
        target_namespace="org/acme",
    )
    assert not denied.promoted and "RBAC denied" in denied.reason

    # Promote to prod as ADMIN but with a FAILING compliance gate → blocked.
    blocked = promote_skill(
        source_client=stg_admin_client,
        target_client=_client(prod_app, prod_admin["token"]),
        name="demo/tool",
        version="1.0.0",
        target_namespace="org/acme",
        require_compliance=True,
        compliance_ok=False,
        compliance_reason="art-14-1-b non-compliant",
    )
    assert not blocked.promoted and "Compliance gate" in blocked.reason

    # A caller-supplied "passed" boolean is not trusted evidence. Until a
    # signed verifier exists, compliance-gated promotion fails closed.
    unverified = promote_skill(
        source_client=stg_admin_client,
        target_client=_client(prod_app, prod_admin["token"]),
        name="demo/tool",
        version="1.0.0",
        target_namespace="org/acme",
        require_compliance=True,
        compliance_ok=True,
    )
    assert not unverified.promoted
    assert "no trusted compliance verifier" in unverified.reason
