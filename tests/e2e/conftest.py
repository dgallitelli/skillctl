"""Shared fixtures for Milestone 0 end-to-end tests.

These tests use a REAL filesystem, a REAL (in-process) registry server,
and REAL crypto (SHA-256 content addressing, HMAC-SHA256 audit chain).
No mocks, no monkeypatching of internals.

Store isolation is achieved by running the ``skillctl`` CLI as a
subprocess with ``HOME`` pointed at a temp directory, so the
content-addressed store resolves to ``<tmp>/.skillctl`` at process
start. The registry is exercised in-process via FastAPI's TestClient
with an explicit HMAC key so we can verify the audit chain.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Mark every test in this package as an integration test so the core
# unit run (`pytest -m 'not integration'`) skips them.
pytestmark = pytest.mark.integration


def _find_skillctl() -> str:
    bin_dir = Path(sys.executable).parent
    candidate = bin_dir / "skillctl"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("skillctl")
    if found:
        return found
    raise FileNotFoundError("skillctl not on PATH — run pip install -e .")


SKILLCTL = _find_skillctl()
KNOWN_HMAC_KEY = b"milestone-0-e2e-hmac-key"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """An isolated HOME so the local store lives under <tmp>/.skillctl."""
    h = tmp_path / "home"
    h.mkdir()
    return h


def run_cli(args, *, home: Path, extra_env: dict | None = None, cwd: str | None = None):
    """Run the real skillctl CLI as a subprocess with an isolated HOME."""
    env = {**os.environ, "HOME": str(home)}
    # Don't let an ambient registry leak into store-only tests.
    env.pop("SKILLCTL_REGISTRY_URL", None)
    env.pop("SKILLCTL_REGISTRY_TOKEN", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [SKILLCTL, *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=cwd,
    )


def write_skill(
    directory: Path,
    *,
    name: str = "e2e-org/demo-skill",
    version: str = "1.0.0",
    description: str = "A demo skill for end-to-end governance tests.",
    body: str = "# Demo Skill\n\nDo the thing safely.\n",
    capabilities=("read_file",),
) -> Path:
    """Write a minimal, valid skill (skill.yaml + SKILL.md) into *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    caps = "\n".join(f"    - {c}" for c in capabilities)
    (directory / "skill.yaml").write_text(
        "apiVersion: skillctl.io/v1\n"
        "kind: Skill\n"
        "metadata:\n"
        f"  name: {name}\n"
        f"  version: {version}\n"
        f'  description: "{description}"\n'
        "spec:\n"
        "  content:\n"
        "    path: SKILL.md\n"
        "  capabilities:\n"
        f"{caps}\n"
    )
    (directory / "SKILL.md").write_text(body)
    return directory


@pytest.fixture
def registry(tmp_path: Path):
    """An in-process registry server with a known HMAC key.

    Yields a dict with the TestClient, the FastAPI app, the data dir, and
    the HMAC key so tests can verify the on-disk blob and audit chain.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from skillctl.registry.api import api_router
    from skillctl.registry.audit import AuditLogger
    from skillctl.registry.auth import AuthManager
    from skillctl.registry.db import MetadataDB
    from skillctl.registry.storage import FilesystemBackend

    data_dir = tmp_path / "registry"
    data_dir.mkdir()

    app = FastAPI()
    app.include_router(api_router)

    db = MetadataDB(data_dir / "registry.db", check_same_thread=False)
    db.initialize()
    app.state.db = db
    app.state.storage = FilesystemBackend(data_dir)
    app.state.audit = AuditLogger(data_dir / "audit.jsonl", hmac_key=KNOWN_HMAC_KEY)
    app.state.auth_manager = AuthManager(db, disabled=True)

    client = TestClient(app)
    try:
        yield {
            "client": client,
            "app": app,
            "data_dir": data_dir,
            "hmac_key": KNOWN_HMAC_KEY,
        }
    finally:
        db.close()
