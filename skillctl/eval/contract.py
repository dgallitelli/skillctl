"""Deterministic schema-contract validation for skill governance.

Milestone 0 replaced the non-deterministic LLM-as-judge functional
evaluation with this module. Every check here is a pure function of the
skill's manifest — running it a thousand times yields the same result,
which is a hard requirement for governance gating.

The contract score (0.0–1.0) is the fraction of applicable checks that
pass. It feeds the unified report as the 20% counterpart to the 80%
security-audit weight.

Checks (all deterministic):
  1. Manifest is parseable (YAML frontmatter / skill.yaml loads).
  2. Required fields present: name, version, description.
  3. Version is valid semver.
  4. allowed_tools / capabilities is a list (when present).
  5. No conflicting metadata (e.g. both inline + path content).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from skillctl.manifest import ManifestLoader
from skillctl.validator import SEMVER_PATTERN


@dataclass
class ContractCheck:
    """A single deterministic contract check result."""

    name: str
    passed: bool
    detail: str = ""
    required: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "required": self.required,
        }


@dataclass
class ContractResult:
    """Aggregate result of all contract checks for a skill."""

    skill_path: str
    checks: list[ContractCheck] = field(default_factory=list)

    @property
    def applicable(self) -> list[ContractCheck]:
        return self.checks

    @property
    def score(self) -> float:
        """Fraction of applicable checks that passed (0.0–1.0)."""
        if not self.checks:
            return 0.0
        passed = sum(1 for c in self.checks if c.passed)
        return passed / len(self.checks)

    @property
    def passed(self) -> bool:
        """All *required* checks must pass for the contract to hold."""
        return all(c.passed for c in self.checks if c.required)

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
        }


def validate_contract(skill_path: str) -> ContractResult:
    """Run all deterministic contract checks against a skill.

    Args:
        skill_path: Path to the skill directory, skill.yaml, or SKILL.md.

    Returns:
        A :class:`ContractResult`. Never raises for skill-content reasons —
        an unparseable manifest is reported as a failed check, not an
        exception, so the evaluator can always produce a score.
    """
    path = Path(skill_path).resolve()
    result = ContractResult(skill_path=str(path))

    # --- Check 1: manifest parseable ---
    loader = ManifestLoader()
    try:
        manifest, _ = loader.load(str(path))
    except Exception as exc:
        result.checks.append(
            ContractCheck(
                name="manifest_parseable",
                passed=False,
                detail=f"Manifest could not be parsed: {exc}",
                required=True,
            )
        )
        # Without a manifest, no further checks are meaningful.
        return result

    result.checks.append(
        ContractCheck(
            name="manifest_parseable",
            passed=True,
            detail="Manifest loaded successfully.",
            required=True,
        )
    )

    md = manifest.metadata

    # --- Check 2: required fields present ---
    missing = [f for f, v in (("name", md.name), ("version", md.version), ("description", md.description)) if not v]
    result.checks.append(
        ContractCheck(
            name="required_fields_present",
            passed=not missing,
            detail=(
                "All required fields (name, version, description) present."
                if not missing
                else f"Missing required field(s): {', '.join(missing)}"
            ),
            required=True,
        )
    )

    # --- Check 3: version is valid semver ---
    semver_ok = bool(md.version) and bool(SEMVER_PATTERN.match(md.version))
    result.checks.append(
        ContractCheck(
            name="version_valid_semver",
            passed=semver_ok,
            detail=(
                f"Version '{md.version}' is valid semver."
                if semver_ok
                else f"Version '{md.version}' is not valid semver (expected MAJOR.MINOR.PATCH)."
            ),
            required=True,
        )
    )

    # --- Check 4: capabilities / allowed_tools is a list (when present) ---
    caps = manifest.spec.capabilities
    caps_ok = isinstance(caps, list)
    result.checks.append(
        ContractCheck(
            name="allowed_tools_is_list",
            passed=caps_ok,
            detail=(
                f"Declared capabilities is a list ({len(caps)} entr{'y' if len(caps) == 1 else 'ies'})."
                if caps_ok
                else "spec.capabilities must be a list."
            ),
            required=False,
        )
    )

    # --- Check 5: no conflicting metadata (content ref) ---
    content = manifest.spec.content
    conflict = bool(content.path and content.inline)
    result.checks.append(
        ContractCheck(
            name="no_conflicting_metadata",
            passed=not conflict,
            detail=(
                "No conflicting content reference."
                if not conflict
                else "spec.content declares both 'path' and 'inline'."
            ),
            required=True,
        )
    )

    return result


def contract_grade(score: float) -> str:
    """Map a 0–1 contract score to a letter grade (mirrors audit grading)."""
    if score >= 0.9:
        return "A"
    elif score >= 0.8:
        return "B"
    elif score >= 0.7:
        return "C"
    elif score >= 0.6:
        return "D"
    return "F"
