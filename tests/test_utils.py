"""Tests for skillctl utils — parse_ref and read_skill_name_from_frontmatter."""

from __future__ import annotations


import pytest

from skillctl.errors import SkillctlError
from skillctl.utils import parse_ref, read_skill_name_from_frontmatter


# -- parse_ref with valid input ----------------------------------------------


def test_parse_ref_valid():
    """parse_ref splits 'ns/name@1.0.0' into (name, version)."""
    name, version = parse_ref("ns/name@1.0.0")
    assert name == "ns/name"
    assert version == "1.0.0"


def test_parse_ref_valid_with_prerelease():
    """parse_ref handles semver pre-release tags."""
    name, version = parse_ref("my-org/my-skill@2.0.0-beta.1")
    assert name == "my-org/my-skill"
    assert version == "2.0.0-beta.1"


# -- parse_ref without "@" raises E_BAD_REF ---------------------------------


def test_parse_ref_no_at_sign():
    """Missing '@' raises E_BAD_REF."""
    with pytest.raises(SkillctlError) as exc_info:
        parse_ref("ns/name-1.0.0")

    assert exc_info.value.code == "E_BAD_REF"


# -- parse_ref with empty version raises E_BAD_REF --------------------------


def test_parse_ref_empty_version():
    """Trailing '@' with no version raises E_BAD_REF."""
    with pytest.raises(SkillctlError) as exc_info:
        parse_ref("ns/name@")

    assert exc_info.value.code == "E_BAD_REF"


# -- parse_ref with empty name raises E_BAD_REF -----------------------------


def test_parse_ref_empty_name():
    """Leading '@' with no name raises E_BAD_REF."""
    with pytest.raises(SkillctlError) as exc_info:
        parse_ref("@1.0.0")

    assert exc_info.value.code == "E_BAD_REF"


# -- parse_ref with multiple "@" uses rsplit correctly -----------------------


def test_parse_ref_multiple_at_signs():
    """Multiple '@' in ref: rsplit takes only the last one as the version."""
    name, version = parse_ref("ns/n@me@1.0.0")
    assert name == "ns/n@me"
    assert version == "1.0.0"


# -- read_skill_name_from_frontmatter with valid SKILL.md -------------------


def test_read_frontmatter_valid(tmp_path):
    """Extracts skill name from SKILL.md YAML frontmatter."""
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text('---\nname: "my-org/my-skill"\ndescription: A skill\n---\n# Skill Content\n')

    result = read_skill_name_from_frontmatter(tmp_path)
    assert result == "my-org/my-skill"


def test_read_frontmatter_unquoted_name(tmp_path):
    """Extracts skill name without quotes from frontmatter."""
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: cool-org/cool-skill\nversion: 1.0.0\n---\n# Body\n")

    result = read_skill_name_from_frontmatter(tmp_path)
    assert result == "cool-org/cool-skill"


# -- read_skill_name_from_frontmatter with no SKILL.md ----------------------


def test_read_frontmatter_no_file(tmp_path):
    """Returns None when SKILL.md does not exist."""
    result = read_skill_name_from_frontmatter(tmp_path)
    assert result is None


# -- read_skill_name_from_frontmatter with no frontmatter -------------------


def test_read_frontmatter_no_frontmatter(tmp_path):
    """Returns None when SKILL.md exists but has no YAML frontmatter."""
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("# Just a Markdown File\nNo frontmatter here.\n")

    result = read_skill_name_from_frontmatter(tmp_path)
    assert result is None


def test_read_frontmatter_empty_frontmatter(tmp_path):
    """Returns None when frontmatter has no name field."""
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\ndescription: no name field\n---\n# Body\n")

    result = read_skill_name_from_frontmatter(tmp_path)
    assert result is None
