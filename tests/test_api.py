"""Integration tests for REST API endpoints — Task 6.9.

Tests the full publish→search→pull→delete lifecycle, auth flows, and error cases
using FastAPI TestClient with a minimal app wired up directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillctl.artifact import build_artifact, build_minimal_artifact, inspect_artifact
from skillctl.manifest import ManifestLoader
from skillctl.registry.api import api_router
from skillctl.registry.audit import AuditLogger
from skillctl.registry.auth import AuthManager
from skillctl.registry.db import MetadataDB
from skillctl.registry.rbac.engine import RBACEngine
from skillctl.registry.rbac.models import Role
from skillctl.registry.rbac.store import RBACStore
from skillctl.registry.storage import FilesystemBackend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_app(tmp_path: Path, auth_disabled: bool = True) -> FastAPI:
    """Create a minimal FastAPI app with all state objects wired up."""
    app = FastAPI()
    app.include_router(api_router)

    db = MetadataDB(tmp_path / "test.db", check_same_thread=False)
    db.initialize()
    storage = FilesystemBackend(tmp_path)
    audit = AuditLogger(tmp_path / "audit.jsonl", hmac_key=b"test-key")
    auth_manager = AuthManager(db, disabled=auth_disabled)

    app.state.db = db
    app.state.storage = storage
    app.state.audit = audit
    app.state.auth_manager = auth_manager

    return app


@pytest.fixture
def app(tmp_path):
    """App with auth disabled for most tests."""
    a = _create_app(tmp_path, auth_disabled=True)
    yield a
    a.state.db.close()


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth_app(tmp_path):
    """App with auth enabled."""
    a = _create_app(tmp_path, auth_disabled=False)
    yield a
    a.state.db.close()


@pytest.fixture
def auth_client(auth_app):
    return TestClient(auth_app)


@pytest.fixture
def rbac_app(tmp_path):
    """App with identity-bound RBAC enabled."""
    app = _create_app(tmp_path, auth_disabled=False)
    store = RBACStore(app.state.db.conn)
    store.initialize()
    app.state.rbac_store = store
    app.state.rbac_engine = RBACEngine(store)
    yield app
    app.state.db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_MANIFEST = {
    "apiVersion": "skillctl.io/v1",
    "kind": "Skill",
    "metadata": {
        "name": "my-org/code-reviewer",
        "version": "1.0.0",
        "description": "Reviews code for quality",
        "authors": [{"name": "Alice", "email": "alice@example.com"}],
        "license": "MIT",
        "tags": ["code-review", "quality"],
    },
    "spec": {
        "content": {"inline": "Review the code carefully."},
    },
}

SKILL_CONTENT = b"# Code Reviewer\nReview code for quality issues."


def _publish(
    client: TestClient, manifest: dict | None = None, content: bytes | None = None, headers: dict | None = None
):
    """Helper to create and publish a skill."""
    m = manifest or VALID_MANIFEST
    c = content or SKILL_CONTENT
    response = client.post(
        "/api/v1/skills",
        data={"manifest": json.dumps(m)},
        files={"content": ("SKILL.md", c, "application/octet-stream")},
        headers=headers or {},
    )
    if response.status_code == 201:
        client.post(
            "/api/v1/skills/publish",
            json={"name": m["metadata"]["name"], "version": m["metadata"]["version"]},
            headers=headers or {},
        )
    return response


def _role_client(app: FastAPI, username: str, role: Role, namespace: str) -> TestClient:
    store: RBACStore = app.state.rbac_store
    user_id = store.create_user(username, "test-password")
    store.add_assignment(user_id, role, namespace, assigned_by="test")
    raw_token, _ = store.create_token(user_id, name=f"{username}-token", scopes=["*"])
    return TestClient(app, headers={"Authorization": f"Bearer {raw_token}"})


def _create_in_namespace(client: TestClient, name: str, namespace: str, version: str = "1.0.0"):
    manifest = {
        **VALID_MANIFEST,
        "metadata": {
            **VALID_MANIFEST["metadata"],
            "name": name,
            "version": version,
        },
    }
    return client.post(
        "/api/v1/skills",
        data={"manifest": json.dumps(manifest), "namespace": namespace},
        files={"content": ("SKILL.md", SKILL_CONTENT, "application/octet-stream")},
    )


# ---------------------------------------------------------------------------
# 6.7 Health check
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_ok(self, client: TestClient):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["storage_status"] == "unknown"
        assert "version" in data
        assert data["skills_count"] == 0

    def test_health_reports_degraded_storage(self, app):
        app.state.storage_consistency = SimpleNamespace(status="degraded")

        with TestClient(app) as client:
            response = client.get("/api/v1/health")

        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["storage_status"] == "degraded"

    def test_health_reflects_skill_count(self, client: TestClient):
        _publish(client)
        resp = client.get("/api/v1/health")
        assert resp.json()["skills_count"] == 1


# ---------------------------------------------------------------------------
# 6.1 Publish skill
# ---------------------------------------------------------------------------


class TestPublish:
    def test_publish_returns_201(self, client: TestClient):
        resp = _publish(client)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "my-org/code-reviewer"
        assert data["version"] == "1.0.0"
        assert data["namespace"] == "my-org"
        assert data["content_hash"]  # non-empty
        assert data["artifact_hash"]  # complete artifact identity
        assert data["tags"] == ["code-review", "quality"]

    def test_publish_preserves_complete_artifact(self, client: TestClient, tmp_path: Path):
        skill = tmp_path / "complete"
        (skill / "scripts").mkdir(parents=True)
        (skill / "references").mkdir()
        manifest_dict = {
            **VALID_MANIFEST,
            "spec": {"content": {"path": "SKILL.md"}},
        }
        (skill / "skill.yaml").write_text(json.dumps(manifest_dict))
        (skill / "SKILL.md").write_bytes(SKILL_CONTENT)
        (skill / "scripts" / "run.py").write_text("print('ok')\n")
        (skill / "references" / "guide.md").write_text("# Guide\n")
        manifest = ManifestLoader()._dict_to_manifest(manifest_dict)
        artifact = build_artifact(skill, manifest, SKILL_CONTENT)

        response = client.post(
            "/api/v1/skills",
            data={"manifest": json.dumps(manifest_dict)},
            files={
                "content": ("SKILL.md", SKILL_CONTENT, "application/octet-stream"),
                "artifact": (
                    "artifact.zip",
                    artifact,
                    "application/vnd.skillctl.artifact.v1+zip",
                ),
            },
        )

        assert response.status_code == 201
        downloaded = client.get("/api/v1/skills/my-org/code-reviewer/1.0.0/artifact")
        assert downloaded.status_code == 200
        verified = inspect_artifact(downloaded.content)
        assert verified.file("scripts/run.py").content == b"print('ok')\n"
        assert verified.file("references/guide.md").content == b"# Guide\n"

    def test_publish_rejects_artifact_content_mismatch(self, client: TestClient):
        manifest = ManifestLoader()._dict_to_manifest(VALID_MANIFEST)
        artifact = build_minimal_artifact(manifest, b"different content")

        response = client.post(
            "/api/v1/skills",
            data={"manifest": json.dumps(VALID_MANIFEST)},
            files={
                "content": ("SKILL.md", SKILL_CONTENT, "application/octet-stream"),
                "artifact": ("artifact.zip", artifact, "application/zip"),
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "E_ARTIFACT_CONTENT_MISMATCH"

    def test_publish_duplicate_returns_409(self, client: TestClient):
        resp1 = _publish(client)
        assert resp1.status_code == 201
        resp2 = _publish(client)
        assert resp2.status_code == 409

    def test_publish_different_version_succeeds(self, client: TestClient):
        _publish(client)
        manifest_v2 = {**VALID_MANIFEST, "metadata": {**VALID_MANIFEST["metadata"], "version": "1.1.0"}}
        resp = _publish(client, manifest=manifest_v2)
        assert resp.status_code == 201
        assert resp.json()["version"] == "1.1.0"

    def test_publish_invalid_manifest_returns_400(self, client: TestClient):
        bad_manifest = {
            "apiVersion": "wrong",
            "kind": "Skill",
            "metadata": {"name": "", "version": "bad"},
            "spec": {"content": {"inline": "x"}},
        }
        resp = _publish(client, manifest=bad_manifest)
        assert resp.status_code == 400

    def test_publish_invalid_json_returns_400(self, client: TestClient):
        resp = client.post(
            "/api/v1/skills",
            data={"manifest": "not-json{{{"},
            files={"content": ("SKILL.md", b"content", "application/octet-stream")},
        )
        assert resp.status_code == 400

    def test_publish_creates_audit_entry(self, app, client: TestClient):
        # M1: POST /skills now records a "skill.created" event (the
        # create/publish split moves "skill.published" to /skills/publish).
        _publish(client)
        events = app.state.audit.read(action="skill.created")
        assert len(events) == 1
        assert events[0].resource == "my-org/code-reviewer@1.0.0"


# ---------------------------------------------------------------------------
# 6.2 List/search skills
# ---------------------------------------------------------------------------


class TestSearch:
    def test_list_empty(self, client: TestClient):
        resp = client.get("/api/v1/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert data["skills"] == []
        assert data["total"] == 0

    def test_list_after_publish(self, client: TestClient):
        _publish(client)
        resp = client.get("/api/v1/skills")
        data = resp.json()
        assert data["total"] == 1
        assert data["skills"][0]["name"] == "my-org/code-reviewer"

    def test_search_by_query(self, client: TestClient):
        _publish(client)
        resp = client.get("/api/v1/skills", params={"q": "code"})
        data = resp.json()
        assert data["total"] >= 1

    def test_search_by_namespace(self, client: TestClient):
        _publish(client)
        resp = client.get("/api/v1/skills", params={"namespace": "my-org"})
        assert resp.json()["total"] == 1
        resp2 = client.get("/api/v1/skills", params={"namespace": "other-org"})
        assert resp2.json()["total"] == 0

    def test_search_by_tag(self, client: TestClient):
        _publish(client)
        resp = client.get("/api/v1/skills", params={"tag": "code-review"})
        assert resp.json()["total"] == 1
        resp2 = client.get("/api/v1/skills", params={"tag": "nonexistent"})
        assert resp2.json()["total"] == 0

    def test_search_pagination(self, client: TestClient):
        # Publish multiple skills
        for i in range(5):
            m = {
                **VALID_MANIFEST,
                "metadata": {**VALID_MANIFEST["metadata"], "name": f"my-org/skill-{i}", "version": "1.0.0"},
            }
            _publish(client, manifest=m)

        resp = client.get("/api/v1/skills", params={"limit": 2, "offset": 0})
        data = resp.json()
        assert len(data["skills"]) == 2
        assert data["total"] == 5
        assert data["limit"] == 2
        assert data["offset"] == 0


# ---------------------------------------------------------------------------
# 6.3 Skill detail
# ---------------------------------------------------------------------------


class TestDetail:
    def test_get_skill_detail(self, client: TestClient):
        _publish(client)
        resp = client.get("/api/v1/skills/my-org/code-reviewer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "my-org/code-reviewer"
        assert data["namespace"] == "my-org"
        assert "1.0.0" in data["versions"]

    def test_get_skill_version(self, client: TestClient):
        _publish(client)
        resp = client.get("/api/v1/skills/my-org/code-reviewer/1.0.0")
        assert resp.status_code == 200
        assert resp.json()["version"] == "1.0.0"

    def test_get_skill_not_found(self, client: TestClient):
        resp = client.get("/api/v1/skills/no-org/nothing")
        assert resp.status_code == 404

    def test_get_skill_version_not_found(self, client: TestClient):
        _publish(client)
        resp = client.get("/api/v1/skills/my-org/code-reviewer/9.9.9")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 6.4 Content download
# ---------------------------------------------------------------------------


class TestContentDownload:
    def test_download_content(self, client: TestClient):
        _publish(client)
        resp = client.get("/api/v1/skills/my-org/code-reviewer/1.0.0/content")
        assert resp.status_code == 200
        assert (
            "text/markdown" in resp.headers["content-type"]
            or resp.headers["content-type"] == "application/octet-stream"
        )
        assert resp.content == SKILL_CONTENT

    def test_download_not_found(self, client: TestClient):
        resp = client.get("/api/v1/skills/no-org/nothing/1.0.0/content")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 6.5 Delete skill
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_skill(self, client: TestClient):
        _publish(client)
        resp = client.delete("/api/v1/skills/my-org/code-reviewer/1.0.0")
        assert resp.status_code == 204

        # Verify it's gone
        resp2 = client.get("/api/v1/skills/my-org/code-reviewer/1.0.0")
        assert resp2.status_code == 404

    def test_delete_not_found(self, client: TestClient):
        resp = client.delete("/api/v1/skills/no-org/nothing/1.0.0")
        assert resp.status_code == 404

    def test_delete_creates_audit_entry(self, app, client: TestClient):
        _publish(client)
        client.delete("/api/v1/skills/my-org/code-reviewer/1.0.0")
        events = app.state.audit.read(action="skill.deleted")
        assert len(events) == 1


# ---------------------------------------------------------------------------
# 6.6 Attach eval
# ---------------------------------------------------------------------------


class TestEval:
    def test_attach_eval(self, client: TestClient):
        _publish(client)
        resp = client.put(
            "/api/v1/skills/my-org/code-reviewer/1.0.0/eval",
            json={"grade": "A", "score": 95.0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["eval_grade"] == "A"
        assert data["eval_score"] == 95.0

    def test_attach_eval_not_found(self, client: TestClient):
        resp = client.put(
            "/api/v1/skills/no-org/nothing/1.0.0/eval",
            json={"grade": "B", "score": 80.0},
        )
        assert resp.status_code == 404

    def test_attach_eval_invalid_grade(self, client: TestClient):
        _publish(client)
        resp = client.put(
            "/api/v1/skills/my-org/code-reviewer/1.0.0/eval",
            json={"grade": "Z", "score": 50.0},
        )
        assert resp.status_code == 422  # Pydantic validation

    def test_attach_eval_score_out_of_range(self, client: TestClient):
        _publish(client)
        resp = client.put(
            "/api/v1/skills/my-org/code-reviewer/1.0.0/eval",
            json={"grade": "A", "score": 150.0},
        )
        assert resp.status_code == 422

    def test_attach_eval_creates_audit_entry(self, app, client: TestClient):
        _publish(client)
        client.put(
            "/api/v1/skills/my-org/code-reviewer/1.0.0/eval",
            json={"grade": "B", "score": 85.0},
        )
        events = app.state.audit.read(action="eval.attached")
        assert len(events) == 1


# ---------------------------------------------------------------------------
# 6.8 Token management
# ---------------------------------------------------------------------------


class TestTokenManagement:
    def test_create_token(self, client: TestClient):
        resp = client.post(
            "/api/v1/tokens",
            json={"name": "ci-bot", "permissions": ["read", "write:my-org"]},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "ci-bot"
        assert data["token"]  # non-empty
        assert data["token_id"]  # non-empty
        assert data["permissions"] == ["read", "write:my-org"]

    def test_create_token_with_expiry(self, client: TestClient):
        resp = client.post(
            "/api/v1/tokens",
            json={"name": "temp", "permissions": ["read"], "expires_in_days": 30},
        )
        assert resp.status_code == 201
        assert resp.json()["expires_at"] is not None

    def test_revoke_token(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/tokens",
            json={"name": "to-revoke", "permissions": ["read"]},
        )
        token_id = create_resp.json()["token_id"]
        resp = client.delete(f"/api/v1/tokens/{token_id}")
        assert resp.status_code == 204

    def test_revoke_nonexistent_token(self, client: TestClient):
        resp = client.delete("/api/v1/tokens/no-such-id")
        assert resp.status_code == 404

    def test_token_create_audit(self, app, client: TestClient):
        client.post(
            "/api/v1/tokens",
            json={"name": "audited", "permissions": ["read"]},
        )
        events = app.state.audit.read(action="token.created")
        assert len(events) == 1

    def test_token_revoke_audit(self, app, client: TestClient):
        create_resp = client.post(
            "/api/v1/tokens",
            json={"name": "revoke-audit", "permissions": ["read"]},
        )
        token_id = create_resp.json()["token_id"]
        client.delete(f"/api/v1/tokens/{token_id}")
        events = app.state.audit.read(action="token.revoked")
        assert len(events) == 1


# ---------------------------------------------------------------------------
# Auth flows (auth enabled)
# ---------------------------------------------------------------------------


class TestAuthFlows:
    def test_missing_token_returns_401(self, auth_client: TestClient):
        resp = auth_client.get("/api/v1/skills")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, auth_client: TestClient):
        resp = auth_client.get(
            "/api/v1/skills",
            headers={"Authorization": "Bearer bad-token"},
        )
        assert resp.status_code == 401

    def test_read_token_can_search(self, auth_app, auth_client: TestClient):
        auth_mgr: AuthManager = auth_app.state.auth_manager
        raw = auth_mgr.create_token("reader", ["read"])
        resp = auth_client.get(
            "/api/v1/skills",
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200

    def test_read_token_cannot_publish(self, auth_app, auth_client: TestClient):
        auth_mgr: AuthManager = auth_app.state.auth_manager
        raw = auth_mgr.create_token("reader", ["read"])
        resp = _publish(auth_client, headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 403

    def test_write_token_can_publish(self, auth_app, auth_client: TestClient):
        auth_mgr: AuthManager = auth_app.state.auth_manager
        raw = auth_mgr.create_token("writer", ["write:my-org"])
        resp = _publish(auth_client, headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 201

    def test_write_wrong_namespace_returns_403(self, auth_app, auth_client: TestClient):
        auth_mgr: AuthManager = auth_app.state.auth_manager
        raw = auth_mgr.create_token("writer", ["write:other-org"])
        resp = _publish(auth_client, headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 403

    def test_admin_can_create_tokens(self, auth_app, auth_client: TestClient):
        auth_mgr: AuthManager = auth_app.state.auth_manager
        raw = auth_mgr.create_token("admin", ["admin"])
        resp = auth_client.post(
            "/api/v1/tokens",
            json={"name": "new-token", "permissions": ["read"]},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 201

    def test_non_admin_cannot_create_tokens(self, auth_app, auth_client: TestClient):
        auth_mgr: AuthManager = auth_app.state.auth_manager
        raw = auth_mgr.create_token("reader", ["read"])
        resp = auth_client.post(
            "/api/v1/tokens",
            json={"name": "new-token", "permissions": ["read"]},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 403

    def test_health_no_auth_required(self, auth_client: TestClient):
        # Health endpoint should not require auth
        resp = auth_client.get("/api/v1/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Registry trust boundaries
# ---------------------------------------------------------------------------


class TestRegistryTrustBoundaries:
    def test_publisher_cannot_mint_legacy_admin_token(self, rbac_app):
        publisher = _role_client(rbac_app, "publisher", Role.PUBLISHER, "*")

        resp = publisher.post(
            "/api/v1/tokens",
            json={"name": "escalated", "permissions": ["admin"]},
        )

        assert resp.status_code == 403
        assert "cannot delegate" in json.dumps(resp.json()).lower()

    def test_publisher_can_delegate_equivalent_scoped_write(self, rbac_app):
        publisher = _role_client(rbac_app, "publisher", Role.PUBLISHER, "*")

        resp = publisher.post(
            "/api/v1/tokens",
            json={"name": "team-writer", "permissions": ["write:org/team-a"]},
        )

        assert resp.status_code == 201

    def test_artifact_uses_stored_rbac_namespace_for_publish(self, rbac_app):
        team_a = _role_client(rbac_app, "team-a-publisher", Role.PUBLISHER, "org/team-a")
        team_b = _role_client(rbac_app, "team-b-publisher", Role.PUBLISHER, "org/team-b")

        created = _create_in_namespace(team_a, "catalog/tool", "org/team-a")
        assert created.status_code == 201
        assert created.json()["namespace"] == "org/team-a"

        denied = team_b.post(
            "/api/v1/skills/publish",
            json={"name": "catalog/tool", "version": "1.0.0", "namespace": "org/team-b"},
        )
        assert denied.status_code == 403

        published = team_a.post(
            "/api/v1/skills/publish",
            json={"name": "catalog/tool", "version": "1.0.0", "namespace": "org/team-a"},
        )
        assert published.status_code == 200

    def test_publish_transition_is_idempotent_and_audited_once(self, rbac_app):
        publisher = _role_client(rbac_app, "idempotent-publisher", Role.PUBLISHER, "org/team-a")
        created = _create_in_namespace(publisher, "catalog/once", "org/team-a")
        assert created.status_code == 201

        body = {
            "name": "catalog/once",
            "version": "1.0.0",
            "namespace": "org/team-a",
        }
        first = publisher.post("/api/v1/skills/publish", json=body)
        second = publisher.post("/api/v1/skills/publish", json=body)

        assert first.status_code == 200
        assert first.json()["changed"] is True
        assert second.status_code == 200
        assert second.json()["changed"] is False
        events = rbac_app.state.audit.read(action="skill.published")
        matching = [event for event in events if event.resource == "catalog/once@1.0.0"]
        assert len(matching) == 1

    def test_namespace_cannot_change_between_versions(self, rbac_app):
        team_a = _role_client(rbac_app, "team-a-owner", Role.PUBLISHER, "org/team-a")
        global_admin = _role_client(rbac_app, "global-admin", Role.ADMIN, "*")

        assert _create_in_namespace(team_a, "catalog/stable", "org/team-a").status_code == 201
        moved = _create_in_namespace(global_admin, "catalog/stable", "org/team-b", version="2.0.0")

        assert moved.status_code == 409
        assert "namespace" in json.dumps(moved.json()).lower()

    def test_viewer_cannot_discover_or_download_draft(self, rbac_app):
        author = _role_client(rbac_app, "author", Role.AUTHOR, "org/team-a")
        viewer = _role_client(rbac_app, "viewer", Role.VIEWER, "org/team-a")

        created = _create_in_namespace(author, "catalog/draft", "org/team-a")
        assert created.status_code == 201

        listing = viewer.get("/api/v1/skills", params={"namespace": "org/team-a"})
        assert listing.status_code == 200
        assert listing.json()["total"] == 0

        assert viewer.get("/api/v1/skills/catalog/draft/1.0.0").status_code == 404
        assert viewer.get("/api/v1/skills/catalog/draft/1.0.0/content").status_code == 404
        assert (
            viewer.get(
                "/api/v1/skills",
                params={"namespace": "org/team-a", "include_drafts": "true"},
            ).status_code
            == 403
        )

        author_listing = author.get(
            "/api/v1/skills",
            params={"namespace": "org/team-a", "include_drafts": "true"},
        )
        assert author_listing.status_code == 200
        assert author_listing.json()["total"] == 1
        assert author.get("/api/v1/skills/catalog/draft/1.0.0").status_code == 200

    def test_remote_registry_rejects_bare_skill_name(self, rbac_app):
        author = _role_client(rbac_app, "bare-author", Role.AUTHOR, "org/team-a")

        created = _create_in_namespace(author, "bare-name", "org/team-a")

        assert created.status_code == 400
        assert "namespace" in json.dumps(created.json()).lower()


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_publish_search_pull_eval_delete(self, client: TestClient):
        # 1. Publish
        pub_resp = _publish(client)
        assert pub_resp.status_code == 201
        content_hash = pub_resp.json()["content_hash"]

        # 2. Search
        search_resp = client.get("/api/v1/skills", params={"q": "code"})
        assert search_resp.status_code == 200
        assert search_resp.json()["total"] >= 1

        # 3. Get detail
        detail_resp = client.get("/api/v1/skills/my-org/code-reviewer")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["content_hash"] == content_hash

        # 4. Download content
        content_resp = client.get("/api/v1/skills/my-org/code-reviewer/1.0.0/content")
        assert content_resp.status_code == 200
        assert content_resp.content == SKILL_CONTENT

        # 5. Download and verify the complete artifact.
        artifact_resp = client.get("/api/v1/skills/my-org/code-reviewer/1.0.0/artifact")
        assert artifact_resp.status_code == 200
        assert inspect_artifact(artifact_resp.content).content_hash == content_hash

        # 6. Attach eval
        eval_resp = client.put(
            "/api/v1/skills/my-org/code-reviewer/1.0.0/eval",
            json={"grade": "A", "score": 97.5},
        )
        assert eval_resp.status_code == 200
        assert eval_resp.json()["eval_grade"] == "A"

        # 7. Verify eval shows in detail
        detail2 = client.get("/api/v1/skills/my-org/code-reviewer/1.0.0")
        assert detail2.json()["eval_grade"] == "A"
        assert detail2.json()["eval_score"] == 97.5

        # 8. Delete
        del_resp = client.delete("/api/v1/skills/my-org/code-reviewer/1.0.0")
        assert del_resp.status_code == 204

        # 9. Verify gone
        gone_resp = client.get("/api/v1/skills/my-org/code-reviewer/1.0.0")
        assert gone_resp.status_code == 404

        # 10. Health still works
        health_resp = client.get("/api/v1/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["skills_count"] == 0
