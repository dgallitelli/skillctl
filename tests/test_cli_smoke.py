"""CLI smoke tests — verify the actual `skillctl` entry point works."""

import os
import subprocess
import sys
from pathlib import Path


def _find_skillctl() -> str:
    """Find the skillctl binary next to the running Python interpreter."""
    bin_dir = Path(sys.executable).parent
    candidate = bin_dir / "skillctl"
    if candidate.exists():
        return str(candidate)
    import shutil

    found = shutil.which("skillctl")
    if found:
        return found
    raise FileNotFoundError("skillctl not on PATH — run pip install -e .")


SKILLCTL = _find_skillctl()


def _run(args: list[str], timeout: int = 10, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        [SKILLCTL] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        **kwargs,
    )


class TestCLIEntryPoint:
    def test_help(self):
        r = _run(["--help"])
        assert r.returncode == 0
        assert "skillctl" in r.stdout

    def test_version(self):
        r = _run(["version"])
        assert r.returncode == 0

    def test_validate_valid(self):
        r = _run(["validate", "tests/fixtures/valid_skill.yaml"])
        assert r.returncode == 0

    def test_validate_invalid(self):
        r = _run(["validate", "tests/fixtures/invalid_skills/bad_semver.yaml"])
        assert r.returncode != 0 or "warning" in r.stdout.lower() or "error" in r.stdout.lower()

    def test_get_skills_local(self):
        r = _run(["get", "skills"])
        assert r.returncode == 0

    def test_eval_audit(self):
        r = _run(["eval", "audit", "plugin/skills/skill-lifecycle"])
        assert r.returncode == 0

    def test_eval_help(self):
        r = _run(["eval"])
        assert r.returncode in (0, 1)

    def test_unknown_command(self):
        r = _run(["nonexistent-command"])
        assert r.returncode != 0 or "usage" in r.stdout.lower() or r.stdout == ""

    def test_create_skill(self, tmp_path):
        r = _run(["create", "skill", "test/smoke-test"], cwd=str(tmp_path))
        assert r.returncode == 0
        assert (tmp_path / "skill.yaml").exists()
        assert (tmp_path / "SKILL.md").exists()
        assert (tmp_path / "evals" / "evals.json").exists()
        assert (tmp_path / "evals" / "eval_queries.json").exists()


class TestPluginHint:
    def test_hint_emitted_when_claudecode_set(self):
        env = os.environ.copy()
        env["CLAUDECODE"] = "1"
        r = _run(["--help"], env=env)
        assert "claude-code-hint" in r.stderr

    def test_hint_not_emitted_normally(self):
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        r = _run(["--help"], env=env)
        assert "claude-code-hint" not in r.stderr


class TestApplyNamespaceGate:
    def test_apply_bare_name_blocked(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: bare-name\ndescription: test\n---\n\nBody")
        r = _run(["apply", "--local", str(skill_dir)])
        assert r.returncode != 0
        assert "namespace" in r.stderr.lower()

    def test_apply_namespaced_name_works(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: code-reviewer\ndescription: test\nskillctl:\n  namespace: test-org\n  version: 0.1.0\n---\n\nBody"
        )
        r = _run(["apply", "--local", "--dry-run", str(skill_dir)])
        assert r.returncode == 0


class TestInstallCLI:
    def test_install_help(self):
        r = _run(["install", "--help"])
        assert r.returncode == 0
        assert "--target" in r.stdout

    def test_uninstall_help(self):
        r = _run(["uninstall", "--help"])
        assert r.returncode == 0
        assert "--target" in r.stdout

    def test_get_installations(self):
        r = _run(["get", "installations"])
        assert r.returncode == 0

    def test_install_from_local_path(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: test\nskillctl:\n  namespace: test-org\n  version: 0.1.0\n---\n\nBody"
        )
        (tmp_path / ".claude").mkdir()
        r = _run(["install", str(skill_dir), "--target", "claude"], cwd=str(tmp_path))
        # Should either succeed or give an actionable error about namespace
        assert r.returncode == 0 or "namespace" in r.stderr.lower()


class TestImportCLI:
    def test_import_help(self):
        r = _run(["import", "--help"])
        assert r.returncode == 0
        assert "archive" in r.stdout

    def test_import_nonexistent_file(self, tmp_path):
        r = _run(["import", str(tmp_path / "nonexistent.tar.gz")])
        assert r.returncode != 0
        assert "not found" in r.stderr.lower() or "archive" in r.stderr.lower()


class TestEvalValidate:
    def test_eval_validate_valid(self):
        r = _run(["eval", "validate", "examples/tdd-workflow"])
        assert r.returncode == 0
        assert "valid" in r.stdout.lower()

    def test_eval_validate_no_evals(self, tmp_path):
        skill_dir = tmp_path / "empty-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n\nBody")
        r = _run(["eval", "validate", str(skill_dir)])
        assert r.returncode == 0
        assert "not found" in r.stderr.lower() or "no eval files" in r.stdout.lower()

    def test_eval_validate_invalid_json(self, tmp_path):
        skill_dir = tmp_path / "bad-skill"
        skill_dir.mkdir()
        evals_dir = skill_dir / "evals"
        evals_dir.mkdir()
        (evals_dir / "evals.json").write_text("not valid json")
        r = _run(["eval", "validate", str(skill_dir)])
        assert r.returncode == 1
        assert "invalid json" in r.stdout.lower()

    def test_eval_validate_missing_fields(self, tmp_path):
        skill_dir = tmp_path / "incomplete-skill"
        skill_dir.mkdir()
        evals_dir = skill_dir / "evals"
        evals_dir.mkdir()
        (evals_dir / "evals.json").write_text('[{"id": "test-1"}]')
        r = _run(["eval", "validate", str(skill_dir)])
        assert r.returncode == 1
        assert "prompt" in r.stdout.lower()
