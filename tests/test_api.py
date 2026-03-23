"""Integration tests for REST API endpoints — Task 6.9.

Tests the full publish→search→pull→delete lifecycle, auth flows, and error cases
using FastAPI TestClient with a minimal app wired up directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillctl.registry.api import api_router
from skillctl.registry.audit import AuditLogger
from skillctl.registry.auth import AuthManager
from skillctl.registry.db import MetadataDB
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
    return _create_app(tmp_path, auth_disabled=True)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth_app(tmp_path):
    """App with auth enabled."""
    return _create_app(tmp_path, auth_disabled=False)


@pytest.fixture
def auth_client(auth_app):
    return TestClient(auth_app)


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


def _publish(client: TestClient, manifest: dict | None = None, content: bytes | None = None,
             headers: dict | None = None):
    """Helper to publish a skill."""
    m = manifest or VALID_MANIFEST
    c = content or SKILL_CONTENT
    return client.post(
        "/api/v1/skills",
        data={"manifest": json.dumps(m)},
        files={"content": ("SKILL.md", c, "application/octet-stream")},
        headers=headers or {},
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
        assert "version" in data
        assert data["skills_count"] == 0

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
        assert data["tags"] == ["code-review", "quality"]

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
        bad_manifest = {"apiVersion": "wrong", "kind": "Skill", "metadata": {"name": "", "version": "bad"}, "spec": {"content": {"inline": "x"}}}
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
        _publish(client)
        events = app.state.audit.read(action="skill.published")
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
            m = {**VALID_MANIFEST, "metadata": {**VALID_MANIFEST["metadata"],
                 "name": f"my-org/skill-{i}", "version": "1.0.0"}}
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
        assert "text/markdown" in resp.headers["content-type"] or resp.headers["content-type"] == "application/octet-stream"
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

        # 5. Attach eval
        eval_resp = client.put(
            "/api/v1/skills/my-org/code-reviewer/1.0.0/eval",
            json={"grade": "A", "score": 97.5},
        )
        assert eval_resp.status_code == 200
        assert eval_resp.json()["eval_grade"] == "A"

        # 6. Verify eval shows in detail
        detail2 = client.get("/api/v1/skills/my-org/code-reviewer/1.0.0")
        assert detail2.json()["eval_grade"] == "A"
        assert detail2.json()["eval_score"] == 97.5

        # 7. Delete
        del_resp = client.delete("/api/v1/skills/my-org/code-reviewer/1.0.0")
        assert del_resp.status_code == 204

        # 8. Verify gone
        gone_resp = client.get("/api/v1/skills/my-org/code-reviewer/1.0.0")
        assert gone_resp.status_code == 404

        # 9. Health still works
        health_resp = client.get("/api/v1/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["skills_count"] == 0
