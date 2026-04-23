"""Shared utilities used across skillctl subsystems."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from skillctl.errors import SkillctlError


def parse_ref(ref: str) -> tuple[str, str]:
    """Parse 'namespace/name@version' into (name, version)."""
    if "@" not in ref:
        raise SkillctlError(
            code="E_BAD_REF",
            what=f"Invalid reference: {ref}",
            why="Requires a name@version reference",
            fix="Use format: namespace/skill-name@1.0.0",
        )
    name, version = ref.rsplit("@", 1)
    return name, version


def read_skill_name_from_manifest(skill_path: str) -> str:
    """Read the skill name from skill.yaml via ManifestLoader."""
    from skillctl.manifest import ManifestLoader

    loader = ManifestLoader()
    manifest, _ = loader.load(skill_path)
    return manifest.metadata.name or Path(skill_path).name


def read_skill_name_from_frontmatter(skill_path: Path) -> Optional[str]:
    """Try to read the skill name from SKILL.md frontmatter."""
    skill_md = skill_path / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        content = skill_md.read_text()
        if content.startswith("---"):
            end = content.index("---", 3)
            fm_text = content[3:end]
            for line in fm_text.splitlines():
                if line.strip().startswith("name:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
    except (ValueError, IndexError):
        pass
    return None
