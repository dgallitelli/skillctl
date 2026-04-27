"""Tests for skillctl diff — version comparison."""

from __future__ import annotations

import json

import pytest

from skillctl.diff import diff_skills, format_diff
from skillctl.manifest import (
    SkillManifest,
    SkillMetadata,
    SkillSpec,
    ContentRef,
    Parameter,
)
from skillctl.store import ContentStore


@pytest.fixture
def store(tmp_path):
    """Create a ContentStore rooted in a temporary directory."""
    return ContentStore(root=tmp_path)


def _make_manifest(
    name="test-org/test-skill",
    version="1.0.0",
    description="A test skill",
    parameters=None,
    capabilities=None,
):
    return SkillManifest(
        metadata=SkillMetadata(
            name=name,
            version=version,
            description=description,
        ),
        spec=SkillSpec(
            content=ContentRef(inline="placeholder"),
            parameters=parameters or [],
            capabilities=capabilities or ["read_file"],
        ),
    )


# -- Diff two versions with content changes --------------------------------

def test_diff_content_changes(store):
    """Diff detects content differences between two versions."""
    m1 = _make_manifest(version="1.0.0")
    m2 = _make_manifest(version="1.1.0")

    store.push(m1, b"# My Skill\nOld instruction\n")
    store.push(m2, b"# My Skill\nNew instruction\nAdded line\n")

    result = diff_skills(
        store,
        "test-org/test-skill@1.0.0",
        "test-org/test-skill@1.1.0",
    )

    assert result.content_diff != ""
    assert "-Old instruction" in result.content_diff
    assert "+New instruction" in result.content_diff
    assert "+Added line" in result.content_diff


# -- Diff detects removed parameters as breaking change --------------------

def test_diff_removed_parameter_breaking(store):
    """Removing a parameter is flagged as a breaking change."""
    m1 = _make_manifest(
        version="1.0.0",
        parameters=[
            Parameter(name="strictness", type="enum", values=["low", "medium", "high"]),
            Parameter(name="verbose", type="boolean"),
        ],
    )
    m2 = _make_manifest(
        version="2.0.0",
        parameters=[
            Parameter(name="verbose", type="boolean"),
        ],
    )

    store.push(m1, b"# Skill v1\n")
    store.push(m2, b"# Skill v2\n")

    result = diff_skills(
        store,
        "test-org/test-skill@1.0.0",
        "test-org/test-skill@2.0.0",
    )

    assert any("strictness" in c and "removed" in c.lower() for c in result.breaking_changes)


# -- Diff detects removed capabilities as breaking change ------------------

def test_diff_removed_capability_breaking(store):
    """Removing a capability is flagged as a breaking change."""
    m1 = _make_manifest(
        version="1.0.0",
        capabilities=["read_file", "write_file"],
    )
    m2 = _make_manifest(
        version="2.0.0",
        capabilities=["read_file"],
    )

    store.push(m1, b"# Skill v1\n")
    store.push(m2, b"# Skill v2\n")

    result = diff_skills(
        store,
        "test-org/test-skill@1.0.0",
        "test-org/test-skill@2.0.0",
    )

    assert any("write_file" in c and "removed" in c.lower() for c in result.breaking_changes)


# -- Diff with --json output -----------------------------------------------

def test_diff_json_output(store):
    """DiffResult.to_dict() produces valid JSON-serializable output."""
    m1 = _make_manifest(version="1.0.0", description="old desc")
    m2 = _make_manifest(version="1.1.0", description="new desc")

    store.push(m1, b"# Skill v1\n")
    store.push(m2, b"# Skill v1\nExtra line\n")

    result = diff_skills(
        store,
        "test-org/test-skill@1.0.0",
        "test-org/test-skill@1.1.0",
    )

    output = result.to_dict()
    # Should be JSON-serializable
    serialized = json.dumps(output)
    parsed = json.loads(serialized)

    assert parsed["ref_a"] == "test-org/test-skill@1.0.0"
    assert parsed["ref_b"] == "test-org/test-skill@1.1.0"
    assert "metadata_changes" in parsed
    assert "breaking_changes" in parsed
    assert "content_diff" in parsed


# -- format_diff produces readable output ----------------------------------

def test_format_diff_output(store):
    """format_diff produces human-readable output with all sections."""
    m1 = _make_manifest(
        version="1.0.0",
        capabilities=["read_file", "write_file"],
    )
    m2 = _make_manifest(
        version="2.0.0",
        capabilities=["read_file"],
    )

    store.push(m1, b"# Skill v1\n")
    store.push(m2, b"# Skill v2\n")

    result = diff_skills(
        store,
        "test-org/test-skill@1.0.0",
        "test-org/test-skill@2.0.0",
    )

    text = format_diff(result)
    assert "Comparing" in text
    assert "→" in text
    assert "Breaking changes" in text


# -- No content differences -------------------------------------------------

def test_diff_no_content_differences(store):
    """Diff of same content but different versions shows metadata change only."""
    m1 = _make_manifest(version="1.0.0")
    m2 = _make_manifest(version="1.0.1", description="Updated desc")

    store.push(m1, b"# Same content\n")
    store.push(m2, b"# Same content but slightly different\n")

    result = diff_skills(
        store,
        "test-org/test-skill@1.0.0",
        "test-org/test-skill@1.0.1",
    )

    # Version should differ in metadata
    assert "version" in result.metadata_changes
    assert result.metadata_changes["version"] == ("1.0.0", "1.0.1")
