"""Regression coverage for explicit experimental trust boundaries."""

from __future__ import annotations

import sys

import pytest

from skillctl.cicd import render_template
from skillctl.cli import main
from skillctl.experimental import warn_experimental


def test_top_level_help_labels_preview_commands(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["skillctl", "--help"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0
    output = capsys.readouterr().out
    for command in ("policy", "observe", "compliance", "deploy", "forensics", "identity"):
        line = next(line for line in output.splitlines() if line.strip().startswith(command))
        assert "[experimental]" in line
    ci_line = next(line for line in output.splitlines() if line.strip().startswith("ci"))
    assert "[preview]" in ci_line


def test_experimental_warning_names_boundary(capsys):
    warn_experimental("deployment modeling", "No live traffic is routed.")

    assert capsys.readouterr().err == "WARNING: deployment modeling is experimental. No live traffic is routed.\n"


@pytest.mark.parametrize("system", ["github", "gitlab", "jenkins"])
def test_ci_templates_disclaim_compliance_certification(system):
    template = render_template(system).lower()

    assert "starter" in template
    assert "non-certifying" in template
