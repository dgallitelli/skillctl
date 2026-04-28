"""Tests for skillctl manifest — ManifestLoader and SkillManifest."""

from __future__ import annotations

import pytest
import yaml

from skillctl.errors import SkillctlError
from skillctl.manifest import (
    Author,
    ContentRef,
    ManifestLoader,
    Parameter,
    SkillManifest,
    SkillMetadata,
    SkillSpec,
    _parse_frontmatter,
)


def _write_skill_yaml(directory, data: dict) -> None:
    """Write a skill.yaml file into the given directory."""
    path = directory / "skill.yaml"
    with open(path, "w") as f:
        yaml.safe_dump(data, f)


def _valid_manifest_dict(
    name="test-org/test-skill",
    version="1.0.0",
    description="A test skill",
    inline="# Hello",
) -> dict:
    """Build a minimal valid skill.yaml dict."""
    return {
        "apiVersion": "skillctl.io/v1",
        "kind": "Skill",
        "metadata": {
            "name": name,
            "version": version,
            "description": description,
            "authors": [{"name": "Alice", "email": "alice@example.com"}],
            "tags": ["test"],
        },
        "spec": {
            "content": {"inline": inline},
            "capabilities": ["read_file"],
        },
    }


@pytest.fixture
def loader():
    return ManifestLoader()


# -- load() with a valid skill.yaml -----------------------------------------


def test_load_valid_skill_yaml(tmp_path, loader):
    """Loading a directory with a valid skill.yaml produces a SkillManifest."""
    data = _valid_manifest_dict()
    _write_skill_yaml(tmp_path, data)

    manifest, warnings = loader.load(str(tmp_path))

    assert isinstance(manifest, SkillManifest)
    assert manifest.api_version == "skillctl.io/v1"
    assert manifest.kind == "Skill"
    assert manifest.metadata.name == "test-org/test-skill"
    assert manifest.metadata.version == "1.0.0"
    assert manifest.metadata.description == "A test skill"
    assert len(manifest.metadata.authors) == 1
    assert manifest.metadata.authors[0].name == "Alice"
    assert manifest.metadata.authors[0].email == "alice@example.com"
    assert manifest.metadata.tags == ["test"]
    assert manifest.spec.content.inline == "# Hello"
    assert manifest.spec.capabilities == ["read_file"]
    assert warnings == []


# -- load() with SKILL.md only (auto-wrap) ----------------------------------


def test_load_skill_md_auto_wrap(tmp_path, loader):
    """Loading a directory with only SKILL.md auto-wraps and warns."""
    md_content = "# My Skill\nDo something useful."
    (tmp_path / "SKILL.md").write_text(md_content)

    manifest, warnings = loader.load(str(tmp_path))

    assert manifest.spec.content.inline == md_content
    assert manifest.metadata.version == "0.0.0"
    assert manifest.metadata.name == tmp_path.name
    assert len(warnings) == 1
    assert warnings[0].code == "W_AUTO_WRAPPED"


# -- load() with empty YAML raises E_INVALID_YAML ---------------------------


def test_load_empty_yaml_raises(tmp_path, loader):
    """An empty skill.yaml raises SkillctlError with E_INVALID_YAML."""
    (tmp_path / "skill.yaml").write_text("")

    with pytest.raises(SkillctlError) as exc_info:
        loader.load(str(tmp_path))

    assert exc_info.value.code == "E_INVALID_YAML"


# -- load() with unknown author fields raises E_MANIFEST_FIELDS -------------


def test_load_unknown_author_fields_raises(tmp_path, loader):
    """Unknown fields in authors section raises E_MANIFEST_FIELDS."""
    data = _valid_manifest_dict()
    data["metadata"]["authors"] = [{"name": "Alice", "email": "a@b.com", "bogus_field": "oops"}]
    _write_skill_yaml(tmp_path, data)

    with pytest.raises(SkillctlError) as exc_info:
        loader.load(str(tmp_path))

    assert exc_info.value.code == "E_MANIFEST_FIELDS"


# -- load() with non-existent directory raises E_NO_MANIFEST ----------------


def test_load_nonexistent_directory_raises(tmp_path, loader):
    """A directory with no skill.yaml or SKILL.md raises E_NO_MANIFEST."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    with pytest.raises(SkillctlError) as exc_info:
        loader.load(str(empty_dir))

    assert exc_info.value.code == "E_NO_MANIFEST"


# -- load() with .txt file raises E_UNKNOWN_FORMAT --------------------------


def test_load_txt_file_raises(tmp_path, loader):
    """A .txt file raises E_UNKNOWN_FORMAT."""
    txt_file = tmp_path / "skill.txt"
    txt_file.write_text("not a skill")

    with pytest.raises(SkillctlError) as exc_info:
        loader.load(str(txt_file))

    assert exc_info.value.code == "E_UNKNOWN_FORMAT"


# -- resolve_content() with path reference -----------------------------------


def test_resolve_content_path_reference(tmp_path, loader):
    """resolve_content reads content from a file path reference."""
    content_text = "# Resolved content\nLine two."
    (tmp_path / "SKILL.md").write_text(content_text)

    manifest = SkillManifest(
        spec=SkillSpec(content=ContentRef(path="SKILL.md")),
    )

    result = loader.resolve_content(manifest, str(tmp_path))
    assert result == content_text


# -- resolve_content() with inline content -----------------------------------


def test_resolve_content_inline(loader):
    """resolve_content returns inline content directly."""
    inline_text = "# Inline skill content"
    manifest = SkillManifest(
        spec=SkillSpec(content=ContentRef(inline=inline_text)),
    )

    result = loader.resolve_content(manifest, "/unused")
    assert result == inline_text


# -- to_dict() round-trip ----------------------------------------------------


def test_to_dict_round_trip():
    """to_dict serializes a manifest with all expected top-level keys."""
    manifest = SkillManifest(
        api_version="skillctl.io/v1",
        kind="Skill",
        metadata=SkillMetadata(
            name="my-org/my-skill",
            version="2.1.0",
            description="A great skill",
            authors=[Author(name="Bob", email="bob@example.com")],
            license="MIT",
            tags=["productivity", "testing"],
        ),
        spec=SkillSpec(
            content=ContentRef(inline="# Content"),
            parameters=[Parameter(name="mode", type="enum", values=["fast", "slow"])],
            capabilities=["read_file", "write_file"],
        ),
    )

    d = manifest.to_dict()

    assert d["apiVersion"] == "skillctl.io/v1"
    assert d["kind"] == "Skill"
    assert d["metadata"]["name"] == "my-org/my-skill"
    assert d["metadata"]["version"] == "2.1.0"
    assert d["metadata"]["description"] == "A great skill"
    assert d["metadata"]["authors"] == [{"name": "Bob", "email": "bob@example.com"}]
    assert d["metadata"]["license"] == "MIT"
    assert d["metadata"]["tags"] == ["productivity", "testing"]
    assert d["spec"]["content"] == {"inline": "# Content"}
    assert d["spec"]["capabilities"] == ["read_file", "write_file"]
    assert len(d["spec"]["parameters"]) == 1
    assert d["spec"]["parameters"][0]["name"] == "mode"
    assert d["spec"]["parameters"][0]["type"] == "enum"


# -- resolve_content() path traversal protection -----------------------------


def test_resolve_content_rejects_path_traversal(tmp_path, loader):
    """resolve_content blocks paths that escape the skill directory."""
    from skillctl.errors import SkillctlError

    manifest = SkillManifest(
        spec=SkillSpec(content=ContentRef(path="../../etc/passwd")),
    )

    with pytest.raises(SkillctlError) as exc_info:
        loader.resolve_content(manifest, str(tmp_path))
    assert exc_info.value.code == "E_PATH_TRAVERSAL"


def test_resolve_content_allows_subdirectory_path(tmp_path, loader):
    """resolve_content allows paths within subdirectories of the skill dir."""
    sub = tmp_path / "scripts"
    sub.mkdir()
    (sub / "helper.md").write_text("# Helper content")

    manifest = SkillManifest(
        spec=SkillSpec(content=ContentRef(path="scripts/helper.md")),
    )

    result = loader.resolve_content(manifest, str(tmp_path))
    assert result == "# Helper content"


# -- category field round-trip ------------------------------------------------


def test_category_round_trip(tmp_path, loader):
    """A manifest with category: security round-trips through load -> to_dict."""
    data = _valid_manifest_dict()
    data["metadata"]["category"] = "security"
    _write_skill_yaml(tmp_path, data)

    manifest, warnings = loader.load(str(tmp_path))

    assert manifest.metadata.category == "security"
    assert warnings == []

    d = manifest.to_dict()
    assert d["metadata"]["category"] == "security"


def test_category_absent_round_trip(tmp_path, loader):
    """A manifest without category omits it from to_dict output."""
    data = _valid_manifest_dict()
    # No category key at all
    _write_skill_yaml(tmp_path, data)

    manifest, warnings = loader.load(str(tmp_path))

    assert manifest.metadata.category is None
    d = manifest.to_dict()
    assert "category" not in d["metadata"]


# -- _parse_frontmatter() unit tests ------------------------------------------


class TestParseFrontmatter:
    def test_with_frontmatter(self):
        content = "---\nname: my-skill\ndescription: Does stuff\n---\n\n# Body"
        fm, body = _parse_frontmatter(content)
        assert fm["name"] == "my-skill"
        assert fm["description"] == "Does stuff"
        assert body == "# Body"

    def test_no_frontmatter(self):
        content = "# Just markdown\n\nNo frontmatter here."
        fm, body = _parse_frontmatter(content)
        assert fm == {}
        assert body == content

    def test_invalid_yaml(self):
        content = "---\n: broken: yaml:\n---\n\nbody"
        fm, body = _parse_frontmatter(content)
        assert fm == {}
        assert body == content

    def test_skillctl_block(self):
        content = "---\nname: my-skill\nskillctl:\n  namespace: my-org\n  version: 2.0.0\n  category: security\n  tags: [a, b]\n  capabilities: [read_file]\n---\n\n# Body"
        fm, body = _parse_frontmatter(content)
        assert fm["skillctl"]["namespace"] == "my-org"
        assert fm["skillctl"]["version"] == "2.0.0"
        assert fm["skillctl"]["category"] == "security"
        assert body == "# Body"


# -- _wrap_markdown() with frontmatter ----------------------------------------


class TestWrapMarkdownWithFrontmatter:
    def test_full_frontmatter_with_skillctl_block(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text(
            "---\nname: code-reviewer\ndescription: Reviews code\nskillctl:\n  namespace: my-org\n  version: 1.2.0\n  category: security\n  tags: [sec]\n  capabilities: [read_file]\n---\n\n# Instructions"
        )
        loader = ManifestLoader()
        manifest, warnings = loader.load(str(md))
        assert manifest.metadata.name == "my-org/code-reviewer"
        assert manifest.metadata.version == "1.2.0"
        assert manifest.metadata.description == "Reviews code"
        assert manifest.metadata.category == "security"
        assert manifest.metadata.tags == ["sec"]
        assert manifest.spec.capabilities == ["read_file"]
        assert "# Instructions" in manifest.spec.content.inline
        assert "---" not in manifest.spec.content.inline
        assert len(warnings) == 0

    def test_frontmatter_without_skillctl_block(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text("---\nname: simple-skill\ndescription: A simple skill\n---\n\nDo the thing.")
        loader = ManifestLoader()
        manifest, warnings = loader.load(str(md))
        assert manifest.metadata.name == "simple-skill"
        assert manifest.metadata.version == "0.1.0"
        assert manifest.metadata.description == "A simple skill"
        assert len(warnings) == 0

    def test_frontmatter_no_description_warns(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text("---\nname: no-desc\n---\n\nBody here.")
        loader = ManifestLoader()
        manifest, warnings = loader.load(str(md))
        assert manifest.metadata.name == "no-desc"
        assert any(w.code == "W_NO_DESCRIPTION" for w in warnings)

    def test_no_frontmatter_legacy_behavior(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text("# Just instructions\n\nNo frontmatter.")
        loader = ManifestLoader()
        manifest, warnings = loader.load(str(md))
        assert manifest.metadata.version == "0.0.0"
        assert any(w.code == "W_AUTO_WRAPPED" for w in warnings)

    def test_frontmatter_with_authors(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text(
            "---\nname: test\ndescription: test\nskillctl:\n  namespace: org\n  authors:\n    - name: Alice\n      email: alice@example.com\n---\n\nBody"
        )
        loader = ManifestLoader()
        manifest, warnings = loader.load(str(md))
        assert len(manifest.metadata.authors) == 1
        assert manifest.metadata.authors[0].name == "Alice"
        assert manifest.metadata.authors[0].email == "alice@example.com"

    def test_frontmatter_with_string_authors(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text(
            "---\nname: test\ndescription: test\nskillctl:\n  authors:\n    - Bob\n---\n\nBody"
        )
        loader = ManifestLoader()
        manifest, warnings = loader.load(str(md))
        assert len(manifest.metadata.authors) == 1
        assert manifest.metadata.authors[0].name == "Bob"
        assert manifest.metadata.authors[0].email is None

    def test_name_from_directory_when_not_in_frontmatter(self, tmp_path):
        skill_dir = tmp_path / "my-cool-skill"
        skill_dir.mkdir()
        md = skill_dir / "SKILL.md"
        md.write_text("---\ndescription: Has desc but no name\n---\n\nBody.")
        loader = ManifestLoader()
        manifest, warnings = loader.load(str(skill_dir))
        assert manifest.metadata.name == "my-cool-skill"
