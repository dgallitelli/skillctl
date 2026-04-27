"""Tests for skillctl doctor — environment diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path


def _run_doctor(monkeypatch, home_dir):
    """Run cmd_doctor with a mocked home directory and capture output."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home_dir))

    from io import StringIO

    captured = StringIO()

    # Patch sys.stdout to capture output
    monkeypatch.setattr(sys, "stdout", captured)

    # Patch sys.exit to capture exit code
    exit_code = [None]

    def mock_exit(code=0):
        exit_code[0] = code
        raise SystemExit(code)

    monkeypatch.setattr(sys, "exit", mock_exit)

    from skillctl.cli import cmd_doctor

    class FakeArgs:
        pass

    try:
        cmd_doctor(FakeArgs())
    except SystemExit:
        pass

    return captured.getvalue(), exit_code[0]


# -- Doctor runs without errors in a clean environment ---------------------


def test_doctor_clean_environment(monkeypatch, tmp_path):
    """Doctor runs and produces output in a clean environment."""
    # Set up a minimal valid environment
    skillctl_dir = tmp_path / ".skillctl"
    store_dir = skillctl_dir / "store"
    store_dir.mkdir(parents=True)

    index_path = skillctl_dir / "index.json"
    index_path.write_text("[]")

    config_path = skillctl_dir / "config.yaml"
    config_path.write_text("registry:\n  url: null\n")

    output, exit_code = _run_doctor(monkeypatch, tmp_path)

    assert "skillctl doctor" in output
    assert "Python" in output
    assert "warnings" in output or "errors" in output


# -- Doctor detects missing store directory --------------------------------


def test_doctor_missing_store(monkeypatch, tmp_path):
    """Doctor flags missing store directory as a warning (fresh install)."""
    # Empty home — no .skillctl directory at all
    output, exit_code = _run_doctor(monkeypatch, tmp_path)

    assert "not found" in output
    assert exit_code == 0


# -- Doctor detects invalid index ------------------------------------------


def test_doctor_invalid_index(monkeypatch, tmp_path):
    """Doctor flags invalid JSON in the store index."""
    skillctl_dir = tmp_path / ".skillctl"
    store_dir = skillctl_dir / "store"
    store_dir.mkdir(parents=True)

    index_path = skillctl_dir / "index.json"
    index_path.write_text("{invalid json!!")

    output, exit_code = _run_doctor(monkeypatch, tmp_path)

    assert "invalid JSON" in output
    assert exit_code == 1


# -- Doctor reports correct skill count ------------------------------------


def test_doctor_skill_count(monkeypatch, tmp_path):
    """Doctor reports the number of skills in the store."""
    skillctl_dir = tmp_path / ".skillctl"
    store_dir = skillctl_dir / "store" / "ab"
    store_dir.mkdir(parents=True)

    # Create two fake manifest files
    (store_dir / "abc123.manifest.yaml").write_text("name: test1")
    (store_dir / "abd456.manifest.yaml").write_text("name: test2")

    index_path = skillctl_dir / "index.json"
    index_path.write_text("[]")

    config_path = skillctl_dir / "config.yaml"
    config_path.write_text("{}")

    output, exit_code = _run_doctor(monkeypatch, tmp_path)

    assert "2 skills" in output
