"""Tests for skillctl store — ContentStore."""

from __future__ import annotations

import json
import tarfile
import zipfile

import yaml

import pytest

from skillctl.errors import SkillctlError
from skillctl.manifest import (
    ContentRef,
    SkillManifest,
    SkillMetadata,
    SkillSpec,
)
from skillctl.store import ContentStore


def _make_manifest(
    name="test-org/test-skill",
    version="1.0.0",
    description="A test skill",
    tags=None,
) -> SkillManifest:
    """Build a minimal SkillManifest for store tests."""
    return SkillManifest(
        metadata=SkillMetadata(
            name=name,
            version=version,
            description=description,
            tags=tags or [],
        ),
        spec=SkillSpec(
            content=ContentRef(inline="# Placeholder"),
            capabilities=["read_file"],
        ),
    )


@pytest.fixture
def store(tmp_path):
    """Create a ContentStore rooted in a temporary directory."""
    return ContentStore(root=tmp_path)


# -- push then pull returns identical content --------------------------------


def test_push_then_pull(store):
    """Pushing content and pulling it back returns the same bytes."""
    manifest = _make_manifest()
    content = b"# My Skill\nDo something useful.\n"

    result = store.push(manifest, content)
    assert result.created is True
    assert result.size == len(content)

    pulled_content, metadata = store.pull("test-org/test-skill", "1.0.0")
    assert pulled_content == content
    assert metadata["name"] == "test-org/test-skill"
    assert metadata["version"] == "1.0.0"


# -- push same name@version twice raises E_ALREADY_EXISTS -------------------


def test_push_duplicate_raises(store):
    """Pushing the same name@version twice raises E_ALREADY_EXISTS."""
    manifest = _make_manifest()
    store.push(manifest, b"content v1")

    with pytest.raises(SkillctlError) as exc_info:
        store.push(manifest, b"content v1 again")

    assert exc_info.value.code == "E_ALREADY_EXISTS"


# -- pull non-existent skill raises E_NOT_FOUND ------------------------------


def test_pull_nonexistent_raises(store):
    """Pulling a skill that was never pushed raises E_NOT_FOUND."""
    with pytest.raises(SkillctlError) as exc_info:
        store.pull("no-org/no-skill", "0.0.1")

    assert exc_info.value.code == "E_NOT_FOUND"


# -- delete then pull raises E_NOT_FOUND ------------------------------------


def test_delete_then_pull_raises(store):
    """After deleting a skill, pulling it raises E_NOT_FOUND."""
    manifest = _make_manifest()
    store.push(manifest, b"to be deleted")

    # Confirm it exists
    content, _ = store.pull("test-org/test-skill", "1.0.0")
    assert content == b"to be deleted"

    # Delete and verify it's gone
    store.delete_skill("test-org/test-skill", "1.0.0")

    with pytest.raises(SkillctlError) as exc_info:
        store.pull("test-org/test-skill", "1.0.0")

    assert exc_info.value.code == "E_NOT_FOUND"


# -- list_skills with namespace filter ---------------------------------------


def test_list_skills_namespace_filter(store):
    """list_skills filters by namespace prefix."""
    store.push(_make_manifest(name="alpha/skill-a", version="1.0.0"), b"a")
    store.push(_make_manifest(name="alpha/skill-b", version="1.0.0"), b"b")
    store.push(_make_manifest(name="beta/skill-c", version="1.0.0"), b"c")

    alpha_skills = store.list_skills(namespace="alpha")
    assert len(alpha_skills) == 2
    assert all(e.name.startswith("alpha/") for e in alpha_skills)

    beta_skills = store.list_skills(namespace="beta")
    assert len(beta_skills) == 1
    assert beta_skills[0].name == "beta/skill-c"


# -- list_skills with tag filter ---------------------------------------------


def test_list_skills_tag_filter(store):
    """list_skills filters by tag."""
    store.push(
        _make_manifest(name="org/tagged", version="1.0.0", tags=["security", "audit"]),
        b"tagged",
    )
    store.push(
        _make_manifest(name="org/untagged", version="1.0.0", tags=["general"]),
        b"untagged",
    )

    security_skills = store.list_skills(tag="security")
    assert len(security_skills) == 1
    assert security_skills[0].name == "org/tagged"

    general_skills = store.list_skills(tag="general")
    assert len(general_skills) == 1
    assert general_skills[0].name == "org/untagged"

    missing_skills = store.list_skills(tag="nonexistent")
    assert len(missing_skills) == 0


# -- list_versions returns results -------------------------------------------


def test_list_versions(store):
    """list_versions returns all versions of a named skill, newest first."""
    store.push(_make_manifest(name="org/versioned", version="1.0.0"), b"v1")
    store.push(_make_manifest(name="org/versioned", version="1.1.0"), b"v1.1")
    store.push(_make_manifest(name="org/versioned", version="2.0.0"), b"v2")
    # Push a different skill to ensure filtering works
    store.push(_make_manifest(name="org/other", version="1.0.0"), b"other")

    versions = store.list_versions("org/versioned")
    assert len(versions) == 3
    # Sorted newest first (reverse string sort)
    assert versions[0].version == "2.0.0"
    assert versions[1].version == "1.1.0"
    assert versions[2].version == "1.0.0"


# -- dry_run push does not modify filesystem ---------------------------------


def test_dry_run_push(store):
    """A dry_run push returns a PushResult but does not write anything."""
    manifest = _make_manifest()
    result = store.push(manifest, b"dry run content", dry_run=True)

    assert result.hash is not None
    assert result.size == len(b"dry run content")
    assert result.created is True  # Would be new

    # Store should be empty — nothing was written
    all_skills = store.list_skills()
    assert len(all_skills) == 0

    # Pulling should fail
    with pytest.raises(SkillctlError) as exc_info:
        store.pull("test-org/test-skill", "1.0.0")
    assert exc_info.value.code == "E_NOT_FOUND"


# -- _write_manifest creates a YAML file -------------------------------------


def test_write_manifest_creates_yaml(tmp_path, store):
    """_write_manifest writes a valid YAML file."""
    manifest = _make_manifest(name="org/yaml-test", version="3.0.0")
    yaml_path = tmp_path / "test_manifest.yaml"

    store._write_manifest(yaml_path, manifest)

    assert yaml_path.exists()
    with open(yaml_path) as f:
        loaded = yaml.safe_load(f)

    assert loaded["apiVersion"] == "skillctl.io/v1"
    assert loaded["kind"] == "Skill"
    assert loaded["metadata"]["name"] == "org/yaml-test"
    assert loaded["metadata"]["version"] == "3.0.0"


# -- export_skills tests -------------------------------------------------------


def test_export_empty_store_tar(store, tmp_path):
    """Export with no skills produces an archive containing only index.json."""
    output = tmp_path / "export.tar.gz"
    result = store.export_skills(output_path=output, format="tar.gz")

    assert result["skill_count"] == 0
    assert result["format"] == "tar.gz"
    assert output.exists()

    with tarfile.open(str(output), "r:gz") as tar:
        names = tar.getnames()
        assert "index.json" in names
        idx = json.loads(tar.extractfile("index.json").read())
        assert idx == []


def test_export_one_skill_tar(store, tmp_path):
    """Export with one skill produces archive with skill files and valid index."""
    manifest = _make_manifest(name="org/export-test", version="1.0.0")
    content = b"# Export Test Skill\nDo something.\n"
    store.push(manifest, content)

    output = tmp_path / "export.tar.gz"
    result = store.export_skills(output_path=output, format="tar.gz")

    assert result["skill_count"] == 1
    assert result["total_size"] > 0
    assert output.exists()

    with tarfile.open(str(output), "r:gz") as tar:
        names = tar.getnames()
        assert "index.json" in names
        assert "skills/org/export-test@1.0.0/SKILL.md" in names
        assert "skills/org/export-test@1.0.0/skill.yaml" in names

        idx = json.loads(tar.extractfile("index.json").read())
        assert len(idx) == 1
        assert idx[0]["name"] == "org/export-test"
        assert idx[0]["version"] == "1.0.0"
        assert "hash" in idx[0]

        skill_content = tar.extractfile("skills/org/export-test@1.0.0/SKILL.md").read()
        assert skill_content == content


def test_export_namespace_filter(store, tmp_path):
    """Export with namespace filter includes only matching skills."""
    store.push(_make_manifest(name="alpha/skill-a", version="1.0.0"), b"alpha-a")
    store.push(_make_manifest(name="alpha/skill-b", version="1.0.0"), b"alpha-b")
    store.push(_make_manifest(name="beta/skill-c", version="1.0.0"), b"beta-c")

    output = tmp_path / "alpha-export.tar.gz"
    result = store.export_skills(output_path=output, format="tar.gz", namespace="alpha")

    assert result["skill_count"] == 2

    with tarfile.open(str(output), "r:gz") as tar:
        idx = json.loads(tar.extractfile("index.json").read())
        names_in_index = {e["name"] for e in idx}
        assert names_in_index == {"alpha/skill-a", "alpha/skill-b"}


def test_export_tag_filter(store, tmp_path):
    """Export with tag filter includes only matching skills."""
    store.push(
        _make_manifest(name="org/tagged", version="1.0.0", tags=["security"]),
        b"tagged",
    )
    store.push(
        _make_manifest(name="org/untagged", version="1.0.0", tags=["general"]),
        b"untagged",
    )

    output = tmp_path / "security-export.tar.gz"
    result = store.export_skills(output_path=output, format="tar.gz", tag="security")

    assert result["skill_count"] == 1

    with tarfile.open(str(output), "r:gz") as tar:
        idx = json.loads(tar.extractfile("index.json").read())
        assert len(idx) == 1
        assert idx[0]["name"] == "org/tagged"


def test_export_zip_format(store, tmp_path):
    """Export in zip format produces a valid zip archive."""
    manifest = _make_manifest(name="org/zip-test", version="2.0.0")
    store.push(manifest, b"# Zip skill content")

    output = tmp_path / "export.zip"
    result = store.export_skills(output_path=output, format="zip")

    assert result["format"] == "zip"
    assert result["skill_count"] == 1
    assert output.exists()

    with zipfile.ZipFile(str(output), "r") as zf:
        names = zf.namelist()
        assert "index.json" in names
        assert "skills/org/zip-test@2.0.0/SKILL.md" in names
        assert "skills/org/zip-test@2.0.0/skill.yaml" in names

        idx = json.loads(zf.read("index.json"))
        assert len(idx) == 1
        assert idx[0]["name"] == "org/zip-test"


def test_export_invalid_format_raises(store, tmp_path):
    """Export with an unsupported format raises SkillctlError."""
    output = tmp_path / "export.rar"
    with pytest.raises(SkillctlError) as exc_info:
        store.export_skills(output_path=output, format="rar")
    assert exc_info.value.code == "E_INVALID_FORMAT"
