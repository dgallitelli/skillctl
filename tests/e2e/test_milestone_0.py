"""End-to-end tests for Milestone 0: Cleanup & Simplify.

These tests use a REAL filesystem, a REAL (in-process) registry server,
and REAL crypto. No mocks. No monkeypatching. If it passes, governance
actually works.

Run with:  pytest tests/e2e/ -v
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from .conftest import run_cli, write_skill

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def _store_index(home: Path) -> list[dict]:
    index_path = home / ".skillctl" / "index.json"
    assert index_path.is_file(), f"store index not created at {index_path}"
    return json.loads(index_path.read_text())


# ---------------------------------------------------------------------------
# Test 1: Create -> Validate -> Publish -> Verify integrity
# ---------------------------------------------------------------------------


def test_e2e_create_validate_publish(tmp_path: Path, home: Path):
    """Full lifecycle: author a valid skill, validate it, push it to the
    real content-addressed store, and verify the stored blob's SHA-256
    matches its content address byte-for-byte."""
    skill_dir = write_skill(tmp_path / "skill", name="e2e-org/lifecycle", version="1.0.0")

    # Validate — must pass.
    v = run_cli(["validate", str(skill_dir)], home=home)
    assert v.returncode == 0, f"validate failed: {v.stdout}\n{v.stderr}"

    # Publish to the local content-addressed store.
    p = run_cli(["publish", str(skill_dir)], home=home)
    assert p.returncode == 0, f"publish failed: {p.stdout}\n{p.stderr}"

    # The store index records the skill.
    index = _store_index(home)
    entry = next((e for e in index if e["name"] == "e2e-org/lifecycle"), None)
    assert entry is not None, f"skill not in store index: {index}"
    assert entry["version"] == "1.0.0"

    # The content-addressed blob exists and its SHA-256 equals the hash.
    content_hash = entry["hash"]
    blob = home / ".skillctl" / "store" / content_hash[:2] / content_hash
    assert blob.is_file(), f"blob missing at {blob}"
    assert hashlib.sha256(blob.read_bytes()).hexdigest() == content_hash


# ---------------------------------------------------------------------------
# Test 2: Security scanner blocks dangerous skills
# ---------------------------------------------------------------------------


def test_e2e_security_block(tmp_path: Path, home: Path):
    """A skill with hardcoded AWS credentials is flagged CRITICAL by the
    audit and BLOCKED from remote publish, with the threat surfaced."""
    skill_dir = tmp_path / "danger"
    skill_dir.mkdir()
    (skill_dir / "skill.yaml").write_text(
        "apiVersion: skillctl.io/v1\n"
        "kind: Skill\n"
        "metadata:\n"
        "  name: e2e-org/danger\n"
        "  version: 1.0.0\n"
        '  description: "danger"\n'
        "spec:\n"
        "  content:\n"
        "    path: SKILL.md\n"
        "  capabilities:\n"
        "    - read_file\n"
    )
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: danger\n"
        "description: danger\n"
        "---\n\n"
        "# Danger\n\n"
        'AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'
    )

    # Audit: CRITICAL finding, exit code 2, correct threat category.
    a = run_cli(["eval", "audit", str(skill_dir)], home=home)
    assert a.returncode == 2, f"expected critical exit 2, got {a.returncode}\n{a.stdout}"
    assert "SEC-001" in a.stdout
    assert "Secret" in a.stdout  # threat category: hardcoded secret

    # Publish to a configured registry is blocked by the security gate.
    p = run_cli(
        ["publish", str(skill_dir)],
        home=home,
        extra_env={"SKILLCTL_REGISTRY_URL": "http://127.0.0.1:9"},
    )
    combined = p.stdout + p.stderr
    assert "Security gate" in combined and "publish blocked" in combined, combined
    assert "SEC-001" in combined


# ---------------------------------------------------------------------------
# Test 3: Version bump and diff detection
# ---------------------------------------------------------------------------


def test_e2e_bump_and_diff(tmp_path: Path, home: Path):
    """Author v1.0.0, modify it, bump to v1.1.0, publish both, and confirm
    diff reports the version change and the content/metadata delta."""
    skill_dir = write_skill(
        tmp_path / "skill",
        name="e2e-org/evolving",
        version="1.0.0",
        description="The original description.",
        body="# Evolving Skill\n\nOriginal behaviour.\n",
        capabilities=("read_file",),
    )
    assert run_cli(["publish", str(skill_dir)], home=home).returncode == 0

    # Modify: new body content + new description + an added capability.
    write_skill(
        skill_dir,
        name="e2e-org/evolving",
        version="1.0.0",
        description="A revised description with more detail.",
        body="# Evolving Skill\n\nRevised behaviour with a new safeguard step.\n",
        capabilities=("read_file", "write_file"),
    )

    # Bump minor: 1.0.0 -> 1.1.0.
    b = run_cli(["bump", str(skill_dir), "--minor"], home=home)
    assert b.returncode == 0, f"bump failed: {b.stdout}\n{b.stderr}"
    assert "1.1.0" in b.stdout

    assert run_cli(["publish", str(skill_dir)], home=home).returncode == 0

    # Diff the two stored versions.
    d = run_cli(
        ["diff", "e2e-org/evolving@1.0.0", "e2e-org/evolving@1.1.0"],
        home=home,
    )
    assert d.returncode == 0, f"diff failed: {d.stdout}\n{d.stderr}"
    out = d.stdout
    assert "1.0.0" in out and "1.1.0" in out
    # The content change between versions should surface in the diff.
    assert "Revised behaviour" in out or "safeguard" in out


# ---------------------------------------------------------------------------
# Test 4: Registry roundtrip with integrity + HMAC audit verification
# ---------------------------------------------------------------------------


def test_e2e_registry_roundtrip(registry):
    """Publish to the in-process registry, pull the content back, and
    verify: content-hash matches, blob is byte-identical, an audit entry
    exists, and the HMAC audit chain verifies."""
    client = registry["client"]
    data_dir = registry["data_dir"]
    app = registry["app"]

    content = b"# Roundtrip Skill\n\nDeterministic governance content.\n"
    manifest = {
        "apiVersion": "skillctl.io/v1",
        "kind": "Skill",
        "metadata": {
            "name": "e2e-org/roundtrip",
            "version": "1.0.0",
            "description": "roundtrip skill",
            "tags": ["testing"],
        },
        "spec": {"content": {"inline": "placeholder"}},
    }

    resp = client.post(
        "/api/v1/skills",
        data={"manifest": json.dumps(manifest)},
        files={"content": ("SKILL.md", content, "application/octet-stream")},
    )
    assert resp.status_code == 201, resp.text
    published = resp.json()
    content_hash = published["content_hash"]

    # SHA-256 of the original content matches the registry's content hash.
    assert hashlib.sha256(content).hexdigest() == content_hash

    # The blob on disk is byte-for-byte identical.
    blob = data_dir / "blobs" / content_hash[:2] / content_hash
    assert blob.is_file(), f"blob missing at {blob}"
    assert blob.read_bytes() == content

    # Pull the content back via the API.
    pull = client.get("/api/v1/skills/e2e-org/roundtrip/1.0.0/content")
    assert pull.status_code == 200
    assert pull.content == content

    # An audit entry exists and the HMAC chain verifies.
    events = app.state.audit.read(action="skill.published")
    assert len(events) == 1
    assert "e2e-org/roundtrip" in events[0].resource

    valid, invalid, parse_errors = app.state.audit.verify_integrity()
    assert invalid == 0 and parse_errors == 0
    assert valid >= 1


# ---------------------------------------------------------------------------
# Test 5: New deterministic scoring is reproducible
# ---------------------------------------------------------------------------


def test_e2e_deterministic_scoring(tmp_path: Path, home: Path):
    """Run `skillctl eval report` 10 times on the same skill and assert all
    10 runs produce the exact same score. (The old LLM-as-judge would not.)"""
    skill_dir = write_skill(tmp_path / "skill", name="e2e-org/scored", version="2.3.1")

    scores = set()
    grades = set()
    for _ in range(10):
        r = run_cli(["eval", "report", str(skill_dir), "--format", "json"], home=home)
        assert r.returncode in (0, 1), f"unexpected exit {r.returncode}: {r.stderr}"
        data = json.loads(r.stdout)
        scores.add(data["overall_score"])
        grades.add(data["overall_grade"])
        # The score is composed only of deterministic sections.
        assert set(data["sections"].keys()) <= {"audit", "contract"}

    assert len(scores) == 1, f"non-deterministic scores: {scores}"
    assert len(grades) == 1, f"non-deterministic grades: {grades}"


# ---------------------------------------------------------------------------
# Test 6: Minimal schema validation
# ---------------------------------------------------------------------------


def test_e2e_minimal_schema_enforced(tmp_path: Path, home: Path):
    """A skill with an invalid/missing version fails validation; once the
    version is corrected, validation passes."""
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Skill\n\nBody.\n")

    def write_manifest(version_line: str):
        (skill_dir / "skill.yaml").write_text(
            "apiVersion: skillctl.io/v1\n"
            "kind: Skill\n"
            "metadata:\n"
            "  name: e2e-org/schema-skill\n"
            f"{version_line}"
            '  description: "a skill"\n'
            "spec:\n"
            "  content:\n"
            "    path: SKILL.md\n"
            "  capabilities:\n"
            "    - read_file\n"
        )

    # Invalid version — validation must fail.
    write_manifest('  version: "1.0"\n')
    bad = run_cli(["validate", str(skill_dir)], home=home)
    assert bad.returncode != 0, f"expected validation failure, got {bad.stdout}"
    assert "VAL-SEMVER" in (bad.stdout + bad.stderr) or "semver" in (bad.stdout + bad.stderr).lower()

    # Corrected version — validation passes.
    write_manifest('  version: "1.0.0"\n')
    good = run_cli(["validate", str(skill_dir)], home=home)
    assert good.returncode == 0, f"expected validation success, got {good.stdout}\n{good.stderr}"


# ---------------------------------------------------------------------------
# Test 7: MCP plugin exposes exactly 5 tools
# ---------------------------------------------------------------------------


def test_e2e_mcp_plugin_tool_count():
    """The MCP plugin server exposes exactly the 5 core governance tools."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from plugin.scripts.mcp_server import mcp

    tools = mcp._tool_manager.list_tools()
    names = {t.name for t in tools}
    assert names == {"validate", "audit", "bump", "diff", "publish"}, names
    assert len(tools) == 5
