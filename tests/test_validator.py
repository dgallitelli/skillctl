"""Tests for skillctl validator — SchemaValidator."""

from __future__ import annotations

import pytest

from skillctl.manifest import (
    ContentRef,
    Parameter,
    SkillManifest,
    SkillMetadata,
    SkillSpec,
)
from skillctl.validator import SchemaValidator


def _make_manifest(
    api_version="skillctl.io/v1",
    kind="Skill",
    name="test-org/test-skill",
    version="1.0.0",
    description="A test skill",
    content_path=None,
    content_inline="# Hello",
    parameters=None,
    capabilities=None,
) -> SkillManifest:
    """Build a SkillManifest with sensible defaults for validation tests."""
    return SkillManifest(
        api_version=api_version,
        kind=kind,
        metadata=SkillMetadata(
            name=name,
            version=version,
            description=description,
        ),
        spec=SkillSpec(
            content=ContentRef(path=content_path, inline=content_inline),
            parameters=parameters or [],
            capabilities=capabilities or ["read_file"],
        ),
    )


@pytest.fixture
def validator():
    return SchemaValidator()


# -- Valid manifest passes ---------------------------------------------------


def test_valid_manifest_passes(validator):
    """A well-formed manifest passes with no errors or warnings."""
    manifest = _make_manifest()
    result = validator.validate(manifest)

    assert result.valid is True
    assert result.errors == []
    assert result.warnings == []
    assert result.exit_code == 0


# -- Wrong apiVersion -------------------------------------------------------


def test_wrong_api_version(validator):
    """Wrong apiVersion produces VAL-APIVERSION error."""
    manifest = _make_manifest(api_version="skillctl.io/v2")
    result = validator.validate(manifest)

    assert result.valid is False
    codes = [e.code for e in result.errors]
    assert "VAL-APIVERSION" in codes


# -- Invalid semver ----------------------------------------------------------


@pytest.mark.parametrize("bad_version", ["1.0", "v1.0.0", "abc", "1.0.0.0"])
def test_invalid_semver(validator, bad_version):
    """Non-semver version strings produce VAL-SEMVER error."""
    manifest = _make_manifest(version=bad_version)
    result = validator.validate(manifest)

    assert result.valid is False
    codes = [e.code for e in result.errors]
    assert "VAL-SEMVER" in codes


# -- Name format validation --------------------------------------------------


def test_invalid_name_format(validator):
    """Name with uppercase/spaces fails with VAL-NAME-FORMAT."""
    manifest = _make_manifest(name="My Org/My Skill")
    result = validator.validate(manifest)

    assert result.valid is False
    codes = [e.code for e in result.errors]
    assert "VAL-NAME-FORMAT" in codes


def test_bare_name_passes(validator):
    """A bare name like 'code-reviewer' should pass validation (valid=True)."""
    manifest = _make_manifest(name="code-reviewer")
    result = validator.validate(manifest)

    assert result.valid is True
    assert result.errors == []


def test_bare_name_no_namespace_info(validator):
    """A bare name should produce a VAL-NAME-NO-NAMESPACE warning."""
    manifest = _make_manifest(name="code-reviewer")
    result = validator.validate(manifest)

    warning_codes = [w.code for w in result.warnings]
    assert "VAL-NAME-NO-NAMESPACE" in warning_codes


def test_namespaced_name_no_info(validator):
    """A namespaced name like 'my-org/code-reviewer' should NOT produce the info warning."""
    manifest = _make_manifest(name="my-org/code-reviewer")
    result = validator.validate(manifest)

    warning_codes = [w.code for w in result.warnings]
    assert "VAL-NAME-NO-NAMESPACE" not in warning_codes


def test_bare_name_still_validates_format(validator):
    """An invalid bare name like 'INVALID_NAME' should fail with VAL-NAME-FORMAT."""
    manifest = _make_manifest(name="INVALID_NAME")
    result = validator.validate(manifest)

    assert result.valid is False
    codes = [e.code for e in result.errors]
    assert "VAL-NAME-FORMAT" in codes


# -- Empty name --------------------------------------------------------------


def test_empty_name(validator):
    """Empty name fails with VAL-NAME-REQUIRED."""
    manifest = _make_manifest(name="")
    result = validator.validate(manifest)

    assert result.valid is False
    codes = [e.code for e in result.errors]
    assert "VAL-NAME-REQUIRED" in codes


# -- Empty description -------------------------------------------------------


def test_empty_description(validator):
    """Empty description fails with VAL-DESC-REQUIRED."""
    manifest = _make_manifest(description="")
    result = validator.validate(manifest)

    assert result.valid is False
    codes = [e.code for e in result.errors]
    assert "VAL-DESC-REQUIRED" in codes


# -- Enum parameter without values -------------------------------------------


def test_enum_param_without_values(validator):
    """An enum parameter with no values fails with VAL-PARAM-ENUM."""
    manifest = _make_manifest(parameters=[Parameter(name="mode", type="enum", values=[])])
    result = validator.validate(manifest)

    assert result.valid is False
    codes = [e.code for e in result.errors]
    assert "VAL-PARAM-ENUM" in codes


# -- Unknown parameter type --------------------------------------------------


def test_unknown_param_type(validator):
    """A parameter with an unknown type fails with VAL-PARAM-TYPE."""
    manifest = _make_manifest(parameters=[Parameter(name="count", type="integer")])
    result = validator.validate(manifest)

    assert result.valid is False
    codes = [e.code for e in result.errors]
    assert "VAL-PARAM-TYPE" in codes


# -- Both path and inline content --------------------------------------------


def test_content_both_path_and_inline(validator):
    """Having both path and inline content fails with VAL-CONTENT-BOTH."""
    manifest = _make_manifest(content_path="SKILL.md", content_inline="# Inline")
    result = validator.validate(manifest)

    assert result.valid is False
    codes = [e.code for e in result.errors]
    assert "VAL-CONTENT-BOTH" in codes


# -- Neither path nor inline content -----------------------------------------


def test_content_empty(validator):
    """Having neither path nor inline content fails with VAL-CONTENT-EMPTY."""
    manifest = _make_manifest(content_path=None, content_inline=None)
    result = validator.validate(manifest)

    assert result.valid is False
    codes = [e.code for e in result.errors]
    assert "VAL-CONTENT-EMPTY" in codes


# -- Unknown capability produces warning -------------------------------------


def test_unknown_capability_warning(validator):
    """An unknown capability produces a VAL-CAP-UNKNOWN warning (not error)."""
    manifest = _make_manifest(capabilities=["read_file", "teleport"])
    result = validator.validate(manifest)

    # Should still be valid (warnings only)
    assert result.valid is True
    assert result.exit_code == 2  # warnings only
    warning_codes = [w.code for w in result.warnings]
    assert "VAL-CAP-UNKNOWN" in warning_codes


# -- check_capabilities detects undeclared write_file usage ------------------


def test_check_capabilities_detects_write_file(validator):
    """check_capabilities warns when content uses write_file but it is not declared."""
    manifest = _make_manifest(capabilities=["read_file"])
    content = "You should write_file to save the results."

    warnings = validator.check_capabilities(manifest, content)

    assert len(warnings) >= 1
    assert any(w.code == "VAL-CAP" for w in warnings)
    assert any("write_file" in w.message for w in warnings)


def test_check_capabilities_no_warning_when_declared(validator):
    """check_capabilities does not warn when capabilities are properly declared."""
    manifest = _make_manifest(capabilities=["read_file", "write_file"])
    content = "You should write_file to save the results."

    warnings = validator.check_capabilities(manifest, content)

    write_warnings = [w for w in warnings if "write_file" in w.message]
    assert write_warnings == []


# -- Category validation ------------------------------------------------------


def test_known_category_passes(validator):
    """A known category passes validation without warnings."""
    manifest = _make_manifest()
    manifest.metadata.category = "security"
    result = validator.validate(manifest)

    assert result.valid is True
    cat_warnings = [w for w in result.warnings if w.code == "VAL-CAT-UNKNOWN"]
    assert cat_warnings == []


def test_unknown_category_produces_warning(validator):
    """An unknown category produces a VAL-CAT-UNKNOWN warning (not error)."""
    manifest = _make_manifest()
    manifest.metadata.category = "banana"
    result = validator.validate(manifest)

    assert result.valid is True  # warning, not error
    cat_warnings = [w for w in result.warnings if w.code == "VAL-CAT-UNKNOWN"]
    assert len(cat_warnings) == 1
    assert cat_warnings[0].severity == "warning"


def test_missing_category_no_warning(validator):
    """A manifest with no category set produces no category warnings."""
    manifest = _make_manifest()
    assert manifest.metadata.category is None
    result = validator.validate(manifest)

    assert result.valid is True
    cat_warnings = [w for w in result.warnings if w.code == "VAL-CAT-UNKNOWN"]
    assert cat_warnings == []
