"""CI/CD pipeline templates for SkillsOps (Milestone 4).

Ships ready-to-use governance pipelines for GitHub Actions, GitLab CI, and
Jenkins that encode validate → audit → compliance → publish using real
``skillctl`` commands.
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# system → (template filename, default output path)
TEMPLATES = {
    "github": ("github-actions.yml", ".github/workflows/skillsops.yml"),
    "gitlab": ("gitlab-ci.yml", ".gitlab-ci.yml"),
    "jenkins": ("Jenkinsfile", "Jenkinsfile"),
}


def list_systems() -> list[str]:
    return sorted(TEMPLATES)


def render_template(system: str) -> str:
    if system not in TEMPLATES:
        raise ValueError(f"Unknown CI system '{system}'. Choose from: {', '.join(list_systems())}")
    filename, _ = TEMPLATES[system]
    return (_TEMPLATE_DIR / filename).read_text()


def default_output(system: str) -> str:
    return TEMPLATES[system][1]


def write_template(system: str, output: str | None = None) -> str:
    """Write the template for *system* to disk; returns the output path."""
    content = render_template(system)
    out = Path(output or default_output(system))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    return str(out)
