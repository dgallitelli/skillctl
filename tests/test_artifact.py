"""Tests for deterministic complete skill artifacts."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from skillctl.artifact import (
    ARTIFACT_MANIFEST,
    artifact_hash,
    build_artifact,
    build_minimal_artifact,
    extract_artifact,
    inspect_artifact,
)
from skillctl.errors import SkillctlError
from skillctl.manifest import ContentRef, SkillManifest, SkillMetadata, SkillSpec


def _manifest(*, content: ContentRef | None = None) -> SkillManifest:
    return SkillManifest(
        metadata=SkillMetadata(name="acme/reviewer", version="1.2.3"),
        spec=SkillSpec(content=content or ContentRef(path="SKILL.md")),
    )


def test_bundle_is_deterministic_and_preserves_supporting_files(tmp_path: Path):
    skill = tmp_path / "reviewer"
    (skill / "scripts").mkdir(parents=True)
    (skill / "references").mkdir()
    (skill / "SKILL.md").write_text("# Reviewer\n")
    (skill / "scripts" / "run.sh").write_text("#!/bin/sh\n")
    (skill / "scripts" / "run.sh").chmod(0o755)
    (skill / "references" / "policy.md").write_text("# Policy\n")

    first = build_artifact(skill, _manifest(), b"# Reviewer\n")
    second = build_artifact(skill, _manifest(), b"# Reviewer\n")
    artifact = inspect_artifact(
        first,
        expected_name="acme/reviewer",
        expected_version="1.2.3",
        expected_content=b"# Reviewer\n",
    )

    assert first == second
    assert artifact_hash(first) == artifact_hash(second)
    assert {item.path for item in artifact.files} == {
        "SKILL.md",
        "references/policy.md",
        "scripts/run.sh",
        "skill.yaml",
    }
    assert artifact.file("scripts/run.sh").mode == 0o755
    assert [item.path for item in artifact.files] == sorted(item.path for item in artifact.files)


def test_plain_markdown_preserves_frontmatter_and_binds_resolved_content(tmp_path: Path):
    skill = tmp_path / "reviewer"
    skill.mkdir()
    source = b"---\nname: reviewer\ndescription: Test\n---\n\nDo reviews.\n"
    (skill / "SKILL.md").write_bytes(source)
    manifest = _manifest(content=ContentRef(inline="Do reviews."))

    bundle = build_artifact(skill, manifest, b"Do reviews.")
    artifact = inspect_artifact(bundle, expected_content=b"Do reviews.")

    assert artifact.file("SKILL.md").content == source
    assert b"inline: Do reviews." in artifact.file("skill.yaml").content


def test_minimal_bundle_round_trip(tmp_path: Path):
    manifest = _manifest()
    bundle = build_minimal_artifact(manifest, b"# Minimal\n")

    artifact = extract_artifact(bundle, tmp_path / "output")

    assert artifact.name == "acme/reviewer"
    assert (tmp_path / "output" / "SKILL.md").read_bytes() == b"# Minimal\n"
    assert (tmp_path / "output" / "skill.yaml").is_file()


def test_tampered_file_is_rejected():
    bundle = build_minimal_artifact(_manifest(), b"# Trusted\n")
    source = zipfile.ZipFile(io.BytesIO(bundle))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename == "SKILL.md":
                content = b"# Tampered\n"
            target.writestr(info, content)

    with pytest.raises(SkillctlError, match="E_ARTIFACT_INTEGRITY"):
        inspect_artifact(output.getvalue())


def test_undeclared_file_is_rejected():
    bundle = build_minimal_artifact(_manifest(), b"# Trusted\n")
    source = zipfile.ZipFile(io.BytesIO(bundle))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr("extra.txt", b"not declared")

    with pytest.raises(SkillctlError, match="E_INVALID_ARTIFACT"):
        inspect_artifact(output.getvalue())


def test_identity_mismatch_is_rejected():
    bundle = build_minimal_artifact(_manifest(), b"# Trusted\n")

    with pytest.raises(SkillctlError, match="E_ARTIFACT_IDENTITY"):
        inspect_artifact(bundle, expected_name="other/reviewer")


def test_symlink_source_is_rejected(tmp_path: Path):
    skill = tmp_path / "reviewer"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Reviewer\n")
    (skill / "outside.txt").symlink_to(tmp_path / "outside.txt")

    with pytest.raises(SkillctlError, match="E_ARTIFACT_SYMLINK"):
        build_artifact(skill, _manifest(), b"# Reviewer\n")


def test_package_manifest_has_sorted_per_file_hashes():
    bundle = build_minimal_artifact(_manifest(), b"# Trusted\n")
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        package = json.loads(archive.read(ARTIFACT_MANIFEST))

    assert package["schemaVersion"] == "skillctl.io/artifact/v1"
    assert [item["path"] for item in package["files"]] == ["SKILL.md", "skill.yaml"]
    assert all(len(item["sha256"]) == 64 for item in package["files"])
