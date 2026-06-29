#!/usr/bin/env python3
"""MCP stdio server exposing the 5 core SkillsOps governance tools.

Milestone 0 trimmed this server from 14 tools to 5. The governance
surface is intentionally small — validate, audit, bump, diff, publish —
mirroring the gatekeeping lifecycle. Authoring aids (create/scaffold),
store browsing (list/describe/delete), the LLM-as-judge evaluators
(functional/trigger/report), and the optimizer have been removed: the
core CLI still provides browsing/scaffolding, and the optimizer is now a
separate package (skillsops-optimize).

Wraps skillctl as a Python library — no shell-out, structured errors.
"""

from __future__ import annotations

import json
import re
import traceback
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from skillctl.diff import diff_skills
from skillctl.errors import SkillctlError
from skillctl.manifest import ManifestLoader
from skillctl.store import ContentStore
from skillctl.validator import SchemaValidator

mcp = FastMCP(
    "skillctl",
    instructions=(
        "Core SkillsOps governance tools for agent skills: validate, audit, "
        "bump, diff, and publish. Use these to gatekeep skills before they "
        "reach an IDE or registry."
    ),
)

# Shared instances
_loader = ManifestLoader()
_validator = SchemaValidator()


def _store() -> ContentStore:
    return ContentStore()


def _error_response(e: Exception) -> str:
    if isinstance(e, SkillctlError):
        return json.dumps(e.format_json(), indent=2)
    return json.dumps({"error": str(e), "traceback": traceback.format_exc()}, indent=2)


# ---------------------------------------------------------------------------
# 1. validate — schema + capability validation
# ---------------------------------------------------------------------------


@mcp.tool()
def validate(skill_path: str) -> str:
    """Validate a skill manifest (skill.yaml or SKILL.md) against the skillctl schema.

    Checks apiVersion, kind, name format, semver, parameter types, capability
    declarations, and content references. Returns structured validation results.

    Args:
        skill_path: Path to the skill directory, skill.yaml, or SKILL.md file.
    """
    try:
        manifest, load_warnings = _loader.load(skill_path)
        result = _validator.validate(manifest)

        content_text = ""
        try:
            content_text = _loader.resolve_content(
                manifest, str(Path(skill_path).resolve().parent if Path(skill_path).is_file() else skill_path)
            )
        except Exception:
            pass

        cap_warnings = _validator.check_capabilities(manifest, content_text) if content_text else []

        output = {
            "valid": result.valid,
            "exit_code": result.exit_code,
            "errors": [
                {"code": i.code, "message": i.message, "path": i.path, "hint": i.hint, "severity": i.severity}
                for i in result.errors
            ],
            "warnings": [
                {"code": i.code, "message": i.message, "path": i.path, "hint": i.hint, "severity": i.severity}
                for i in result.warnings
            ],
            "capability_warnings": [
                {"code": i.code, "message": i.message, "path": i.path, "hint": i.hint, "severity": i.severity}
                for i in cap_warnings
            ],
            "load_warnings": [str(w) for w in load_warnings],
            "skill_name": manifest.metadata.name,
            "version": manifest.metadata.version,
        }
        return json.dumps(output, indent=2)
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# 2. audit — full security audit
# ---------------------------------------------------------------------------


@mcp.tool()
def audit(
    skill_path: str,
    verbose: bool = False,
    ignore_codes: str = "",
    include_all: bool = False,
) -> str:
    """Run a security audit on a skill, producing an A-F grade and findings.

    Scans for structure issues (STR-*), security vulnerabilities (SEC-*),
    permission problems (PERM-*), and authoring quality (QLT-*). Each finding
    includes a code, severity, title, detail, and suggested fix.

    Args:
        skill_path: Path to the skill directory.
        verbose: Include INFO-level findings.
        ignore_codes: Comma-separated finding codes to suppress (e.g., "STR-017,SEC-002").
        include_all: Scan entire directory tree instead of just skill-standard directories.
    """
    try:
        from skillctl.eval.cli import run_audit

        ignore_set = set(c.strip() for c in ignore_codes.split(",") if c.strip()) if ignore_codes else None
        report = run_audit(
            skill_path,
            verbose=verbose,
            ignore_codes=ignore_set,
            include_all=include_all,
        )
        return json.dumps(
            {
                "skill_name": report.skill_name,
                "skill_path": report.skill_path,
                "score": report.score,
                "grade": report.grade,
                "passed": report.passed,
                "critical_count": report.critical_count,
                "warning_count": report.warning_count,
                "info_count": report.info_count,
                "findings": [f.to_dict() for f in report.findings],
                "metadata": report.metadata,
            },
            indent=2,
        )
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# 3. bump — semver version bump in skill.yaml
# ---------------------------------------------------------------------------


@mcp.tool()
def bump(skill_path: str, level: str = "patch") -> str:
    """Bump a skill's semver version in skill.yaml.

    Args:
        skill_path: Path to the skill directory or skill.yaml file.
        level: One of "major", "minor", or "patch" (default: "patch").
    """
    try:
        if level not in ("major", "minor", "patch"):
            return json.dumps(
                {"success": False, "reason": f"Invalid level '{level}' (use major, minor, or patch)"}, indent=2
            )

        path = Path(skill_path)
        yaml_path = path / "skill.yaml" if path.is_dir() else path
        if not yaml_path.exists():
            return json.dumps({"success": False, "reason": f"No skill.yaml found at {yaml_path}"}, indent=2)

        content = yaml_path.read_text()
        match = re.search(r'version:\s*["\']?(\d+)\.(\d+)\.(\d+)["\']?', content)
        if not match:
            return json.dumps(
                {"success": False, "reason": "Could not find a semver version field in skill.yaml"}, indent=2
            )

        major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if level == "major":
            major, minor, patch = major + 1, 0, 0
        elif level == "minor":
            minor, patch = minor + 1, 0
        else:
            patch += 1

        old_version = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
        new_version = f"{major}.{minor}.{patch}"
        new_content = content.replace(match.group(0), match.group(0).replace(old_version, new_version))
        yaml_path.write_text(new_content)

        return json.dumps(
            {
                "success": True,
                "old_version": old_version,
                "new_version": new_version,
                "level": level,
                "path": str(yaml_path),
            },
            indent=2,
        )
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# 4. diff — compare two skill versions from the local store
# ---------------------------------------------------------------------------


@mcp.tool()
def diff(ref_a: str, ref_b: str) -> str:
    """Compare two skill versions from the local store.

    Shows metadata changes, breaking changes (removed parameters or capabilities),
    and a unified content diff.

    Args:
        ref_a: First skill reference in "namespace/name@version" format.
        ref_b: Second skill reference in "namespace/name@version" format.
    """
    try:
        store = _store()
        result = diff_skills(store, ref_a, ref_b)
        return json.dumps(result.to_dict(), indent=2)
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# 5. publish — validate, security-gate, and push to the store/registry
# ---------------------------------------------------------------------------


@mcp.tool()
def publish(
    skill_path: str,
    dry_run: bool = False,
    local: bool = False,
    registry_url: str | None = None,
    token: str | None = None,
) -> str:
    """Validate and publish a skill to the content-addressed store (and registry).

    Runs full schema validation and a security audit gate, then pushes the
    skill to the local content-addressed store. If a registry is configured
    and local=False, the skill is also published to the remote registry.
    Publishing is BLOCKED on any CRITICAL audit finding.

    Args:
        skill_path: Path to the skill directory or skill.yaml.
        dry_run: If True, validate and compute hash without writing.
        local: If True, skip the remote publish (local store only).
        registry_url: Override the configured registry URL.
        token: Override the configured registry auth token.
    """
    try:
        from skillctl._cli_helpers import apply_skill

        result = apply_skill(
            skill_path,
            dry_run=dry_run,
            local=local,
            registry_url=registry_url,
            token=token,
        )

        push = result.push_result
        out = {
            "success": result.remote_status != "blocked (security)",
            "ref": result.ref,
            "local_status": result.local_status,
            "remote_status": result.remote_status,
            "dry_run": dry_run,
        }
        if push is not None:
            out.update({"hash": push.hash, "path": push.path, "size": push.size, "created": push.created})
        if result.remote_status == "blocked (security)":
            out["reason"] = "security_gate_blocked"
            out["critical_findings"] = [
                {"code": f.code, "title": f.title, "fix": f.fix} for f in result.critical_findings
            ]
            out["hint"] = "Fix CRITICAL findings or use local=True to skip the remote security gate."
        return json.dumps(out, indent=2)
    except Exception as e:
        return _error_response(e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
