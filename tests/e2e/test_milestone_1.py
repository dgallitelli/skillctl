"""End-to-end tests for Milestone 1: RBAC.

Real registry server (subprocess), real SQLite, real token generation, real
permission checks, real HMAC audit chain. No mocks. No monkeypatching.

Run with:  pytest tests/e2e/test_milestone_1.py -v
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration


def _find_skillctl() -> str:
    candidate = Path(sys.executable).parent / "skillctl"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("skillctl")
    if not found:
        raise FileNotFoundError("skillctl not on PATH — run pip install -e .")
    return found


SKILLCTL = _find_skillctl()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RegistryFixture:
    """Manages a real registry server instance for testing."""

    def __init__(self, tmp_path: Path, auth_disabled: bool = False):
        self.tmp_dir = tmp_path
        self.data_dir = tmp_path / "registry-data"
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.auth_disabled = auth_disabled
        self.process: subprocess.Popen | None = None
        self.admin = {"username": None, "password": None, "token": None}
        self._lines: list[str] = []

    def start(self) -> None:
        args = [
            SKILLCTL,
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--data-dir",
            str(self.data_dir),
            "--auto-generate-hmac-key",
            "--log-level",
            "warning",
        ]
        if self.auth_disabled:
            args.append("--auth-disabled")
        self.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._drain, daemon=True).start()
        self._wait_ready()
        self._parse_bootstrap(timeout=5.0)

    def _drain(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            self._lines.append(line.rstrip("\n"))

    def _wait_ready(self, timeout: float = 25.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process and self.process.poll() is not None:
                raise RuntimeError("server exited early:\n" + "\n".join(self._lines))
            try:
                r = httpx.get(f"{self.base_url}/api/v1/health", timeout=1.0)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.15)
        raise RuntimeError("server did not become ready:\n" + "\n".join(self._lines))

    def _parse_bootstrap(self, timeout: float = 5.0) -> None:
        if self.auth_disabled:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            blob = "\n".join(self._lines)
            u = re.search(r"Username:\s*(\S+)", blob)
            p = re.search(r"Password:\s*(\S+)", blob)
            t = re.search(r"Token:\s*(\S+)", blob)
            if u and p and t:
                self.admin = {"username": u.group(1), "password": p.group(1), "token": t.group(1)}
                return
            time.sleep(0.1)
        raise RuntimeError("did not capture bootstrap admin creds:\n" + "\n".join(self._lines))

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def client(self, token: str | None = None) -> httpx.Client:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx.Client(base_url=self.base_url, headers=headers, timeout=10.0)

    # -- high-level helpers --------------------------------------------------

    def admin_client(self) -> httpx.Client:
        return self.client(self.admin["token"])

    def login(self, username: str, password: str) -> str:
        r = httpx.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"username": username, "password": password},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()["token"]

    def create_user_with_role(self, username: str, password: str, role: str, namespace: str) -> str:
        """Admin creates a user and assigns a role; returns a login token."""
        ac = self.admin_client()
        r = ac.post("/api/v1/users", json={"username": username, "password": password})
        assert r.status_code == 201, r.text
        r = ac.post("/api/v1/rbac/assign", json={"username": username, "role": role, "namespace": namespace})
        assert r.status_code == 201, r.text
        return self.login(username, password)


def _manifest(name: str, version: str = "1.0.0") -> str:
    return json.dumps(
        {
            "apiVersion": "skillctl.io/v1",
            "kind": "Skill",
            "metadata": {"name": name, "version": version, "description": "e2e skill"},
            "spec": {"content": {"inline": "x"}},
        }
    )


def _create_skill(
    client: httpx.Client, name: str, namespace: str, version: str = "1.0.0", content: bytes = b"# Skill\n\nBody.\n"
):
    return client.post(
        "/api/v1/skills",
        data={"manifest": _manifest(name, version), "namespace": namespace},
        files={"content": ("SKILL.md", content, "application/octet-stream")},
    )


def _publish(client: httpx.Client, name: str, namespace: str, version: str = "1.0.0"):
    return client.post(
        "/api/v1/skills/publish",
        json={"name": name, "version": version, "namespace": namespace},
    )


@pytest.fixture
def registry(tmp_path):
    fx = RegistryFixture(tmp_path)
    fx.start()
    try:
        yield fx
    finally:
        fx.stop()


@pytest.fixture
def noauth_registry(tmp_path):
    fx = RegistryFixture(tmp_path, auth_disabled=True)
    fx.start()
    try:
        yield fx
    finally:
        fx.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_e2e_rbac_author_cannot_publish(registry):
    token = registry.create_user_with_role("alice", "pw-alice", "author", "org/test")
    alice = registry.client(token)

    # Author can create.
    r = _create_skill(alice, "proj/demo", "org/test")
    assert r.status_code == 201, r.text

    # Author cannot publish.
    r = _publish(alice, "proj/demo", "org/test")
    assert r.status_code == 403
    assert "skill:publish" in json.dumps(r.json())

    # The denial is in the audit chain.
    ac = registry.admin_client()
    events = ac.get("/api/v1/audit", params={"action": "auth_decision", "limit": 1000}).json()["events"]
    denials = [
        e for e in events if e["details"].get("permission") == "skill:publish" and e["details"]["decision"] == "denied"
    ]
    assert denials and denials[-1]["actor"] == "alice"


def test_e2e_rbac_publisher_full_flow(registry):
    token = registry.create_user_with_role("bob", "pw-bob", "publisher", "org/test")
    bob = registry.client(token)

    assert _create_skill(bob, "proj/tool", "org/test").status_code == 201
    r = _publish(bob, "proj/tool", "org/test")
    assert r.status_code == 200, r.text

    ac = registry.admin_client()
    audit = ac.get("/api/v1/audit", params={"action": "skill.published", "limit": 100}).json()
    published = [e for e in audit["events"] if "proj/tool" in e["resource"]]
    assert published and published[-1]["actor"] == "bob"
    # HMAC chain still verifies.
    assert audit["integrity"]["invalid"] == 0


def test_e2e_scoped_token_namespace_isolation(registry):
    ac = registry.admin_client()
    ac.post("/api/v1/users", json={"username": "carol", "password": "pw"})
    ac.post("/api/v1/rbac/assign", json={"username": "carol", "role": "publisher", "namespace": "org/team-a"})
    ac.post("/api/v1/rbac/assign", json={"username": "carol", "role": "publisher", "namespace": "org/team-b"})

    # Carol logs in and mints a token scoped to team-a only.
    carol_login = registry.login("carol", "pw")
    scoped = (
        registry.client(carol_login)
        .post("/api/v1/auth/tokens", json={"name": "team-a-only", "scopes": ["org/team-a"]})
        .json()["token"]
    )
    scoped_client = registry.client(scoped)

    assert _create_skill(scoped_client, "proj/a", "org/team-a").status_code == 201
    r = _create_skill(scoped_client, "proj/b", "org/team-b")
    assert r.status_code == 403
    assert "Token scope" in json.dumps(r.json())


def test_e2e_permission_inheritance(registry):
    token = registry.create_user_with_role("dave", "pw", "admin", "org/acme")
    dave = registry.client(token)

    # Child namespace inherits the org/acme admin grant.
    assert _create_skill(dave, "proj/x", "org/acme/team-ml").status_code == 201
    assert _publish(dave, "proj/x", "org/acme/team-ml").status_code == 200

    # Sibling namespace is NOT covered.
    r = _create_skill(dave, "proj/y", "org/other")
    assert r.status_code == 403


def test_e2e_admin_role_lifecycle(registry):
    ac = registry.admin_client()
    ac.post("/api/v1/users", json={"username": "eve", "password": "pw"})
    eve_token = registry.login("eve", "pw")
    eve = registry.client(eve_token)

    # No roles → cannot read.
    assert eve.get("/api/v1/skills", params={"namespace": "org/test"}).status_code == 403

    # Grant viewer → can read.
    ac.post("/api/v1/rbac/assign", json={"username": "eve", "role": "viewer", "namespace": "org/test"})
    assert eve.get("/api/v1/skills", params={"namespace": "org/test"}).status_code == 200

    # Revoke viewer → cannot read again.
    ac.post("/api/v1/rbac/revoke", json={"username": "eve", "role": "viewer", "namespace": "org/test"})
    assert eve.get("/api/v1/skills", params={"namespace": "org/test"}).status_code == 403


def test_e2e_token_expiry(registry):
    token = registry.create_user_with_role("grace", "pw", "publisher", "org/test")
    short = (
        registry.client(token)
        .post("/api/v1/auth/tokens", json={"name": "short", "scopes": ["*"], "expires_in_seconds": 1})
        .json()["token"]
    )

    # Immediately usable.
    assert registry.client(short).get("/api/v1/auth/whoami").status_code == 200
    time.sleep(2)
    # Expired → 401.
    assert registry.client(short).get("/api/v1/auth/whoami").status_code == 401


def test_e2e_token_revocation(registry):
    token = registry.create_user_with_role("frank", "pw", "publisher", "org/test")
    frank = registry.client(token)
    created = frank.post("/api/v1/auth/tokens", json={"name": "worker", "scopes": ["*"]}).json()
    worker = registry.client(created["token"])

    assert worker.get("/api/v1/auth/whoami").status_code == 200
    # Revoke via the tokens endpoint.
    assert frank.request("DELETE", f"/api/v1/tokens/{created['token_id']}").status_code == 204
    assert worker.get("/api/v1/auth/whoami").status_code == 401


def test_e2e_bootstrap_first_run(registry):
    # Bootstrap creds were captured at startup; they authenticate as admin.
    assert registry.admin["username"] == "admin"
    who = registry.admin_client().get("/api/v1/auth/whoami").json()
    assert who["username"] == "admin"
    assert "admin" in who["roles"]
    # Admin can create another user.
    assert registry.admin_client().post("/api/v1/users", json={"username": "hank", "password": "pw"}).status_code == 201


def test_e2e_backward_compatibility_no_auth(noauth_registry):
    anon = noauth_registry.client()  # no token
    assert _create_skill(anon, "proj/free", "org/open").status_code == 201
    assert _publish(anon, "proj/free", "org/open").status_code == 200

    # Audit still records operations with actor="anonymous".
    audit = anon.get("/api/v1/audit", params={"limit": 200}).json()
    actors = {e["actor"] for e in audit["events"]}
    assert "anonymous" in actors


def test_e2e_unauthenticated_is_rejected(registry):
    # With auth enabled, a missing/invalid token is rejected (not silently allowed).
    assert registry.client().get("/api/v1/skills", params={"namespace": "org/test"}).status_code == 401
    assert registry.client("bogus-token").get("/api/v1/auth/whoami").status_code == 401


def test_e2e_cli_auth_flow(registry, tmp_path):
    """login → whoami → token create → logout, via the real CLI with a temp HOME."""
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "PATH": __import__("os").environ["PATH"]}

    def run(*args):
        return subprocess.run([SKILLCTL, *args], capture_output=True, text=True, env=env, timeout=60)

    # login
    r = run(
        "auth",
        "login",
        "--registry",
        registry.base_url,
        "--username",
        "admin",
        "--password",
        registry.admin["password"],
    )
    assert r.returncode == 0, r.stderr
    creds_file = home / ".skillctl" / "credentials.json"
    assert creds_file.exists()
    assert oct(creds_file.stat().st_mode & 0o777) == "0o600"

    # whoami
    r = run("auth", "whoami", "--registry", registry.base_url)
    assert r.returncode == 0 and "admin" in r.stdout

    # token create
    r = run("auth", "token", "create", "--name", "ci", "--scope", "*", "--registry", registry.base_url)
    assert r.returncode == 0 and "Token created" in r.stdout

    # logout clears creds
    r = run("auth", "logout", "--registry", registry.base_url)
    assert r.returncode == 0
    assert json.loads(creds_file.read_text())["registries"] == {}


def test_e2e_change_password(registry):
    ac = registry.admin_client()
    ac.post("/api/v1/users", json={"username": "iris", "password": "old-pw"})
    token = registry.login("iris", "old-pw")

    r = registry.client(token).post(
        "/api/v1/auth/change-password", json={"old_password": "old-pw", "new_password": "new-pw"}
    )
    assert r.status_code == 200

    # Old password no longer works; new one does.
    assert (
        httpx.post(
            f"{registry.base_url}/api/v1/auth/login", json={"username": "iris", "password": "old-pw"}, timeout=10.0
        ).status_code
        == 401
    )
    assert registry.login("iris", "new-pw")


def test_e2e_concurrent_access_control(registry):
    viewer_t = registry.create_user_with_role("v", "pw", "viewer", "org/c")
    author_t = registry.create_user_with_role("a", "pw", "author", "org/c")
    publisher_t = registry.create_user_with_role("p", "pw", "publisher", "org/c")

    results: dict[str, int] = {}

    def viewer_read():
        results["viewer_read"] = (
            registry.client(viewer_t).get("/api/v1/skills", params={"namespace": "org/c"}).status_code
        )

    def author_create():
        results["author_create"] = _create_skill(registry.client(author_t), "proj/ac", "org/c").status_code

    def author_publish():
        results["author_publish"] = _publish(registry.client(author_t), "proj/ac", "org/c").status_code

    def publisher_create_publish():
        registry.client(publisher_t)
        _create_skill(registry.client(publisher_t), "proj/pp", "org/c")
        results["publisher_publish"] = _publish(registry.client(publisher_t), "proj/pp", "org/c").status_code

    threads = [
        threading.Thread(target=viewer_read),
        threading.Thread(target=author_create),
        threading.Thread(target=publisher_create_publish),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # author must create before publishing (sequential dependency).
    author_publish()

    assert results["viewer_read"] == 200
    assert results["author_create"] == 201
    assert results["author_publish"] == 403  # author cannot publish
    assert results["publisher_publish"] == 200
