"""Integration tests for Web UI — Task 8.5.

Verifies HTML responses contain expected skill data after publishing via the API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from skillctl.registry.api import api_router
from skillctl.registry.audit import AuditLogger
from skillctl.registry.auth import AuthManager
from skillctl.registry.db import MetadataDB
from skillctl.registry.storage import FilesystemBackend
from skillctl.registry.web import web_router

_PACKAGE_DIR = Path(__file__).resolve().parent.parent / "skillctl" / "registry"
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _create_app(tmp_path: Path) -> FastAPI:
    """Create a minimal FastAPI app with both API and web routers."""
    app = FastAPI()
    app.include_router(api_router)
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    db = MetadataDB(tmp_path / "test.db", check_same_thread=False)
    db.initialize()
    storage = FilesystemBackend(tmp_path)
    audit = AuditLogger(tmp_path / "audit.jsonl", hmac_key=b"test-key")
    auth_manager = AuthManager(db, disabled=True)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    app.state.db = db
    app.state.storage = storage
    app.state.audit = audit
    app.state.auth_manager = auth_manager
    app.state.templates = templates

    return app


@pytest.fixture
def app(tmp_path):
    return _create_app(tmp_path)


@pytest.fixture
def client(app):
    return TestClient(app)


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


def _publish(client: TestClient, manifest: dict | None = None, content: bytes | None = None):
    """Publish a skill via the API."""
    m = manifest or VALID_MANIFEST
    c = content or SKILL_CONTENT
    return client.post(
        "/api/v1/skills",
        data={"manifest": json.dumps(m)},
        files={"content": ("SKILL.md", c, "application/octet-stream")},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBrowsePage:
    def test_index_returns_html(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Skill Registry" in resp.text

    def test_index_shows_published_skill(self, client: TestClient):
        _publish(client)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "my-org/code-reviewer" in resp.text
        assert "Reviews code for quality" in resp.text

    def test_index_shows_tags(self, client: TestClient):
        _publish(client)
        resp = client.get("/")
        assert "code-review" in resp.text

    def test_index_with_query_filter(self, client: TestClient):
        _publish(client)
        resp = client.get("/", params={"q": "code"})
        assert resp.status_code == 200
        assert "my-org/code-reviewer" in resp.text


class TestSkillDetailPage:
    def test_detail_returns_html(self, client: TestClient):
        _publish(client)
        resp = client.get("/skills/my-org/code-reviewer")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "my-org/code-reviewer" in resp.text

    def test_detail_shows_metadata(self, client: TestClient):
        _publish(client)
        resp = client.get("/skills/my-org/code-reviewer")
        assert "Reviews code for quality" in resp.text
        assert "MIT" in resp.text
        assert "Alice" in resp.text
        assert "code-review" in resp.text

    def test_detail_shows_content_preview(self, client: TestClient):
        _publish(client)
        resp = client.get("/skills/my-org/code-reviewer")
        assert "Code Reviewer" in resp.text

    def test_detail_shows_version_history(self, client: TestClient):
        _publish(client)
        manifest_v2 = {**VALID_MANIFEST, "metadata": {**VALID_MANIFEST["metadata"], "version": "1.1.0"}}
        _publish(client, manifest=manifest_v2)
        resp = client.get("/skills/my-org/code-reviewer")
        assert "1.0.0" in resp.text
        assert "1.1.0" in resp.text

    def test_detail_not_found(self, client: TestClient):
        resp = client.get("/skills/no-org/nothing")
        assert resp.status_code == 404

    def test_version_detail(self, client: TestClient):
        _publish(client)
        resp = client.get("/skills/my-org/code-reviewer/1.0.0")
        assert resp.status_code == 200
        assert "1.0.0" in resp.text
        assert "my-org/code-reviewer" in resp.text

    def test_version_detail_not_found(self, client: TestClient):
        _publish(client)
        resp = client.get("/skills/my-org/code-reviewer/9.9.9")
        assert resp.status_code == 404

    def test_detail_shows_eval_scores(self, client: TestClient):
        _publish(client)
        client.put(
            "/api/v1/skills/my-org/code-reviewer/1.0.0/eval",
            json={"grade": "A", "score": 95.0},
        )
        resp = client.get("/skills/my-org/code-reviewer")
        assert resp.status_code == 200
        assert ">A<" in resp.text
        assert "95.0" in resp.text


class TestHtmxSearch:
    def test_htmx_search_returns_partial(self, client: TestClient):
        _publish(client)
        resp = client.get(
            "/skills",
            params={"q": "code"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "my-org/code-reviewer" in resp.text
        # Partial should NOT contain full page layout
        assert "<!DOCTYPE html>" not in resp.text

    def test_htmx_search_empty_results(self, client: TestClient):
        resp = client.get(
            "/skills",
            params={"q": "nonexistent"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "No skills found" in resp.text

    def test_non_htmx_skills_redirects(self, client: TestClient):
        resp = client.get("/skills", params={"q": "test"}, follow_redirects=False)
        assert resp.status_code == 302
        assert "q=test" in resp.headers["location"]

    def test_non_htmx_skills_no_params_redirects_to_root(self, client: TestClient):
        resp = client.get("/skills", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"
