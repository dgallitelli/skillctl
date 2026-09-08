"""Tests for ``skillctl._cli_helpers.apply_skill`` — the library form
of the CLI's `apply` lifecycle.

The tests cover:

- Happy path (returns an ``ApplyResult`` with the canonical ref).
- Idempotent re-apply (``local_status == "unchanged"``).
- Dry-run shape.
- Validation failure raises ``SkillctlError(code="E_VALIDATION")`` and
  the CLI shim formats it the same way it always did.
- Namespace gate (bare names + remote → ``E_NO_NAMESPACE``).
- ``cmd_install`` with a local path goes through ``apply_skill``
  end-to-end (behavioural — checks the store contains the skill).

The existing CLI-shim tests at ``tests/test_cli.py`` cover ``cmd_apply``
itself; this file focuses on the library function.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from skillctl._cli_helpers import ApplyResult, apply_skill
from skillctl.errors import SkillctlError


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


VALID_SKILL_YAML = """\
apiVersion: skillctl.io/v1
kind: Skill

metadata:
  name: test-org/test-skill
  version: 0.1.0
  description: A skill for testing apply_skill behaviour

spec:
  content:
    path: ./SKILL.md
  capabilities:
    - read_file
"""

VALID_SKILL_MD = """\
---
name: test-org/test-skill
description: A skill for testing apply_skill behaviour
---

# Test skill

Body.
"""

INVALID_SKILL_YAML = """\
apiVersion: skillctl.io/v1
kind: Skill

metadata:
  name: ""
  version: not-a-version
  description: ""

spec:
  content:
    path: ./SKILL.md
"""

BARE_SKILL_YAML = """\
apiVersion: skillctl.io/v1
kind: Skill

metadata:
  name: bare-skill
  version: 0.1.0
  description: A bare-name skill (no namespace)

spec:
  content:
    path: ./SKILL.md
  capabilities:
    - read_file
"""


def _make_skill(tmp_path: Path, *, yaml_content: str = VALID_SKILL_YAML) -> Path:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "skill.yaml").write_text(yaml_content)
    (skill / "SKILL.md").write_text(VALID_SKILL_MD)
    return skill


# ---------------------------------------------------------------------------
# apply_skill — direct library calls
# ---------------------------------------------------------------------------


class TestApplySkillHappyPath:
    def test_returns_apply_result_with_canonical_ref(self, tmp_path, monkeypatch):
        skill = _make_skill(tmp_path)
        store_root = tmp_path / "store-root"
        # Patch ContentStore via the cli shim so apply_skill picks it up.
        monkeypatch.setattr(
            "skillctl.cli.ContentStore",
            lambda: __import__("skillctl.store", fromlist=["ContentStore"]).ContentStore(store_root),
        )

        result = apply_skill(str(skill), local=True)
        assert isinstance(result, ApplyResult)
        assert result.ref == "test-org/test-skill@0.1.0"
        assert result.local_status == "pushed"
        assert result.remote_status is None
        assert result.push_result is not None
        # Store now has the skill.
        assert (store_root / "index.json").exists()
        index = json.loads((store_root / "index.json").read_text())
        stored = next(e for e in index if e["name"] == "test-org/test-skill")
        assert stored["artifact_hash"]

    def test_idempotent_second_call_unchanged(self, tmp_path, monkeypatch):
        skill = _make_skill(tmp_path)
        store_root = tmp_path / "store-root"
        monkeypatch.setattr(
            "skillctl.cli.ContentStore",
            lambda: __import__("skillctl.store", fromlist=["ContentStore"]).ContentStore(store_root),
        )

        first = apply_skill(str(skill), local=True)
        assert first.local_status == "pushed"

        second = apply_skill(str(skill), local=True)
        assert second.local_status == "unchanged"
        assert second.ref == first.ref

    def test_dry_run_does_not_mutate_store(self, tmp_path, monkeypatch):
        skill = _make_skill(tmp_path)
        store_root = tmp_path / "store-root"
        monkeypatch.setattr(
            "skillctl.cli.ContentStore",
            lambda: __import__("skillctl.store", fromlist=["ContentStore"]).ContentStore(store_root),
        )

        result = apply_skill(str(skill), local=True, dry_run=True)
        assert result.local_status == "dry-run"
        assert result.remote_status is None
        assert result.push_result is not None
        assert result.push_result.size > 0
        # Store unchanged.
        assert not (store_root / "index.json").exists()


# ---------------------------------------------------------------------------
# apply_skill — error paths
# ---------------------------------------------------------------------------


class TestApplySkillErrors:
    def test_validation_failure_raises_E_VALIDATION(self, tmp_path):
        skill = _make_skill(tmp_path, yaml_content=INVALID_SKILL_YAML)
        with pytest.raises(SkillctlError) as exc_info:
            apply_skill(str(skill), local=True)
        err = exc_info.value
        assert err.code == "E_VALIDATION"
        # `why` is a multi-line list of `[CODE] message` entries.
        assert "[" in err.why and "]" in err.why
        # Each line should have the inline-printable shape so cmd_apply
        # can format them.
        lines = err.why.splitlines()
        assert len(lines) >= 1
        assert all(line.lstrip().startswith("[") for line in lines if line.strip())

    def test_bare_name_local_ok(self, tmp_path, monkeypatch):
        # Bare names are fine for the local store; only the remote
        # registry rejects them.
        skill = _make_skill(tmp_path, yaml_content=BARE_SKILL_YAML)
        store_root = tmp_path / "store-root"
        monkeypatch.setattr(
            "skillctl.cli.ContentStore",
            lambda: __import__("skillctl.store", fromlist=["ContentStore"]).ContentStore(store_root),
        )
        result = apply_skill(str(skill), local=True)
        assert result.ref == "bare-skill@0.1.0"
        assert result.local_status == "pushed"

    def test_bare_name_remote_blocked(self, tmp_path, monkeypatch):
        # When a registry is configured AND --local is not passed,
        # bare-name skills are rejected by the namespace gate.
        skill = _make_skill(tmp_path, yaml_content=BARE_SKILL_YAML)
        monkeypatch.setattr("skillctl.cli._get_registry_url", lambda args: "http://fake-registry:8080")

        with pytest.raises(SkillctlError) as exc_info:
            apply_skill(str(skill), local=False)
        assert exc_info.value.code == "E_NO_NAMESPACE"


# ---------------------------------------------------------------------------
# cmd_apply CLI shim — validation-error formatting must be unchanged
# from the pre-refactor experience (no behaviour regression).
# ---------------------------------------------------------------------------


class TestCmdApplyValidationFormatting:
    def test_validation_errors_print_inline_per_error(self, tmp_path):
        skill = _make_skill(tmp_path, yaml_content=INVALID_SKILL_YAML)
        r = subprocess.run(
            [SKILLCTL, "apply", "--local", str(skill)],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 1
        # The inline form is preserved across the refactor.
        assert "Validation errors — cannot apply:" in r.stderr
        assert "✗ [" in r.stderr  # at least one per-error line


# ---------------------------------------------------------------------------
# cmd_install — local path goes through apply_skill (behavioural)
# ---------------------------------------------------------------------------


class TestCmdInstallLocalPath:
    def test_install_local_path_applies_first(self, tmp_path, monkeypatch, capsys):
        # Install a skill from a local path; the implementation calls
        # apply_skill internally so the skill ends up in the local
        # store before the install step writes the IDE files.
        skill = _make_skill(tmp_path)

        # Drop into tmp_path so .claude/ is the install target.
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".claude").mkdir()

        # Redirect the ContentStore so BOTH the apply step (which
        # resolves via skillctl.cli.ContentStore) AND the install
        # step (which imports ContentStore from skillctl.store
        # via `from skillctl.store import ContentStore` at module
        # top of skillctl.install) write to and read from the same
        # fresh store rooted in tmp_path.  Patching the
        # already-imported binding inside skillctl.install is what
        # actually intercepts install_skill()'s `ContentStore()`
        # call.
        store_root = tmp_path / "store-root"
        from skillctl.store import ContentStore as _RealContentStore

        def _store_factory(root=None):
            # Default-instantiated case (apply_skill, install_skill):
            # ignore the absent ``root`` arg and force the test root.
            return _RealContentStore(root if root is not None else store_root)

        monkeypatch.setattr("skillctl.cli.ContentStore", _store_factory)
        monkeypatch.setattr("skillctl.install.ContentStore", _store_factory)

        # Redirect install_skill's tracker default to a fresh path so
        # we don't read or pollute the user's
        # ~/.skillctl/installations.json across test runs.
        from skillctl import install as install_mod

        fresh_tracker = tmp_path / "installations.json"
        original_install = install_mod.install_skill

        def _install_with_fresh_tracker(*args, **kwargs):
            kwargs.setdefault("tracker_path", fresh_tracker)
            return original_install(*args, **kwargs)

        # cmd_install imports install_skill at function-call time
        # (`from skillctl.install import ... install_skill`), so we
        # patch the symbol in the install module before the import
        # resolves.
        monkeypatch.setattr(install_mod, "install_skill", _install_with_fresh_tracker)

        from skillctl.cli import cmd_install

        import argparse

        cmd_install(
            argparse.Namespace(
                ref=str(skill),
                from_url=None,
                target="claude",
                global_scope=False,
                force=False,
                dry_run=False,
            )
        )

        # Behavioural assertions: store has the skill, installation
        # state file exists, the .claude file was written.
        assert (store_root / "index.json").exists()
        index = json.loads((store_root / "index.json").read_text())
        assert any(e["name"] == "test-org/test-skill" for e in index)
        installed = tmp_path / ".claude" / "skills" / "test-skill" / "SKILL.md"
        assert installed.exists()

        # Regression guard: the inner ``apply_skill`` call must still
        # surface a one-line ``✓ Applied ...`` confirmation in stdout.
        # When ``cmd_install`` was first refactored to call
        # ``apply_skill`` directly this line silently disappeared
        # because ``apply_skill`` is library-only.  See
        # ``_print_inner_apply_summary`` in ``skillctl/cli.py``.
        out = capsys.readouterr().out
        assert "✓ Applied test-org/test-skill@0.1.0" in out
        assert "(local only)" in out


# ---------------------------------------------------------------------------
# cmd_logout — typed config round-trip
# ---------------------------------------------------------------------------


class TestCmdLogoutTypedConfig:
    def test_logout_clears_token(self, tmp_path, monkeypatch, capsys):
        # Redirect CONFIG_PATH so we don't touch ~/.skillctl/config.yaml.
        target = tmp_path / "config.yaml"
        from skillctl import config as cfg_mod

        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg_mod, "CONFIG_PATH", target)

        # Seed a config with a github.token set.
        cfg = cfg_mod.SkillctlConfig()
        cfg.github.token = "ghp_abc123"
        cfg_mod.save_config(cfg)
        assert target.exists()

        from skillctl.cli import cmd_logout

        cmd_logout()
        output = capsys.readouterr().out
        assert "✓ GitHub credentials removed." in output

        # File is rewritten with the token gone.
        reloaded = cfg_mod.load_config()
        assert reloaded.github.token is None

    def test_logout_when_not_logged_in(self, tmp_path, monkeypatch, capsys):
        from skillctl import config as cfg_mod

        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.yaml")

        from skillctl.cli import cmd_logout

        cmd_logout()
        assert "Not logged in." in capsys.readouterr().out

    def test_logout_preserves_other_config_fields(self, tmp_path, monkeypatch):
        # The typed-config switch shouldn't drop unrelated fields.
        from skillctl import config as cfg_mod

        target = tmp_path / "config.yaml"
        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg_mod, "CONFIG_PATH", target)

        cfg = cfg_mod.SkillctlConfig()
        cfg.github.token = "ghp_abc123"
        cfg.registry.local.url = "https://my-registry.example.com"
        cfg.optimize.budget_usd = 7.5
        cfg_mod.save_config(cfg)

        from skillctl.cli import cmd_logout

        cmd_logout()

        reloaded = cfg_mod.load_config()
        assert reloaded.github.token is None
        assert reloaded.registry.local.url == "https://my-registry.example.com"
        assert reloaded.optimize.budget_usd == 7.5
