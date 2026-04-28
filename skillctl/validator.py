"""Schema validation and capability checking for SkillManifest."""

import re
from dataclasses import dataclass, field
from typing import Optional

from skillctl.manifest import ContentRef, KNOWN_CAPABILITIES, SkillManifest

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

NAMESPACED_NAME_PATTERN = re.compile(r"^[a-z0-9-]+/[a-z0-9-]+$")
BARE_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")

VALID_PARAM_TYPES = {"string", "number", "boolean", "enum"}

KNOWN_CATEGORIES = {
    "code-review",
    "deployment",
    "documentation",
    "dev-tools",
    "frameworks",
    "security",
    "testing",
    "data",
    "design",
    "infrastructure",
    "observability",
    "general",
}


@dataclass
class ValidationIssue:
    code: str
    message: str
    path: str  # dot-path to offending field
    hint: str
    severity: str = "error"  # "error" or "warning"


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """0=valid, 1=errors, 2=warnings only."""
        if self.errors:
            return 1
        if self.warnings:
            return 2
        return 0


class SchemaValidator:
    """Validates SkillManifest structure, semver, and capabilities."""

    def validate(self, manifest: SkillManifest) -> ValidationResult:
        """Run all validation checks."""
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []

        errors.extend(self._validate_structure(manifest))

        semver_issue = self._validate_semver(manifest.metadata.version)
        if semver_issue:
            errors.append(semver_issue)

        name_error, name_info = self._validate_name(manifest.metadata.name)
        if name_error:
            errors.append(name_error)
        if name_info:
            warnings.append(name_info)

        errors.extend(self._validate_parameters(manifest.spec.parameters))

        content_issue = self._validate_content_ref(manifest.spec.content)
        if content_issue:
            errors.append(content_issue)

        cap_warnings = self._validate_capabilities(manifest.spec.capabilities)
        warnings.extend(cap_warnings)

        cat_warning = self._validate_category(manifest.metadata.category)
        if cat_warning:
            warnings.append(cat_warning)

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def check_capabilities(self, manifest: SkillManifest, content: str) -> list[ValidationIssue]:
        """Check declared capabilities against skill content."""
        warnings: list[ValidationIssue] = []
        declared = set(manifest.spec.capabilities)

        capability_patterns = {
            "write_file": [
                r"\bwrite_file\b",
                r"\bcreate_file\b",
                r"\bsave\s+file\b",
                r"\bwrite\s+to\s+file\b",
                r"\boverwrite\b",
            ],
            "network_access": [
                r"https?://",
                r"\bfetch\b",
                r"\bcurl\b",
                r"\bhttp\s+request\b",
                r"\bapi\s+call\b",
            ],
        }

        for cap, patterns in capability_patterns.items():
            if cap not in declared:
                for pattern in patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        warnings.append(
                            ValidationIssue(
                                code="VAL-CAP",
                                message=f"Content suggests '{cap}' but it's not declared",
                                path="spec.capabilities",
                                hint=f"Add '{cap}' to spec.capabilities in skill.yaml",
                                severity="warning",
                            )
                        )
                        break  # one warning per capability type

        return warnings

    def _validate_structure(self, m: SkillManifest) -> list[ValidationIssue]:
        errors: list[ValidationIssue] = []
        if m.api_version != "skillctl.io/v1":
            errors.append(
                ValidationIssue(
                    code="VAL-APIVERSION",
                    message=f"apiVersion must be 'skillctl.io/v1', got '{m.api_version}'",
                    path="apiVersion",
                    hint="Set apiVersion: skillctl.io/v1",
                )
            )
        if m.kind != "Skill":
            errors.append(
                ValidationIssue(
                    code="VAL-KIND",
                    message=f"kind must be 'Skill', got '{m.kind}'",
                    path="kind",
                    hint="Set kind: Skill",
                )
            )
        if not m.metadata.name:
            errors.append(
                ValidationIssue(
                    code="VAL-NAME-REQUIRED",
                    message="metadata.name is required",
                    path="metadata.name",
                    hint="Add a name like 'my-org/my-skill'",
                )
            )
        if not m.metadata.description:
            errors.append(
                ValidationIssue(
                    code="VAL-DESC-REQUIRED",
                    message="metadata.description is required",
                    path="metadata.description",
                    hint="Add a description of what the skill does",
                )
            )
        return errors

    def _validate_semver(self, version: str) -> Optional[ValidationIssue]:
        if not SEMVER_PATTERN.match(version):
            return ValidationIssue(
                code="VAL-SEMVER",
                message=f"Version '{version}' is not valid semver",
                path="metadata.version",
                hint="Use MAJOR.MINOR.PATCH format, e.g. '1.0.0'",
            )
        return None

    def _validate_name(self, name: str) -> tuple[Optional[ValidationIssue], Optional[ValidationIssue]]:
        if not name:
            return (None, None)
        if NAMESPACED_NAME_PATTERN.match(name):
            return (None, None)
        if BARE_NAME_PATTERN.match(name):
            return (
                None,
                ValidationIssue(
                    code="VAL-NAME-NO-NAMESPACE",
                    message=f"Name '{name}' has no namespace prefix",
                    path="metadata.name",
                    hint="Consider using 'my-org/{}' for registry publishing".format(name),
                    severity="warning",
                ),
            )
        return (
            ValidationIssue(
                code="VAL-NAME-FORMAT",
                message=f"Name '{name}' must be namespace/skill-name (lowercase, hyphens)",
                path="metadata.name",
                hint="Use format like 'my-org/my-skill'",
            ),
            None,
        )

    def _validate_parameters(self, params: list) -> list[ValidationIssue]:
        errors: list[ValidationIssue] = []
        for i, p in enumerate(params):
            if p.type not in VALID_PARAM_TYPES:
                errors.append(
                    ValidationIssue(
                        code="VAL-PARAM-TYPE",
                        message=f"Parameter '{p.name}' has invalid type '{p.type}'",
                        path=f"spec.parameters[{i}].type",
                        hint=f"Use one of: {', '.join(sorted(VALID_PARAM_TYPES))}",
                    )
                )
            if p.type == "enum" and not p.values:
                errors.append(
                    ValidationIssue(
                        code="VAL-PARAM-ENUM",
                        message=f"Enum parameter '{p.name}' has no values",
                        path=f"spec.parameters[{i}].values",
                        hint="Add a 'values' list for enum parameters",
                    )
                )
        return errors

    def _validate_capabilities(self, capabilities: list) -> list[ValidationIssue]:
        warnings: list[ValidationIssue] = []
        for i, cap in enumerate(capabilities):
            if cap not in KNOWN_CAPABILITIES:
                warnings.append(
                    ValidationIssue(
                        code="VAL-CAP-UNKNOWN",
                        message=f"Unknown capability '{cap}'",
                        path=f"spec.capabilities[{i}]",
                        hint=f"Known capabilities: {', '.join(sorted(KNOWN_CAPABILITIES))}",
                        severity="warning",
                    )
                )
        return warnings

    def _validate_category(self, category: Optional[str]) -> Optional[ValidationIssue]:
        if category is not None and category not in KNOWN_CATEGORIES:
            return ValidationIssue(
                code="VAL-CAT-UNKNOWN",
                message=f"Unknown category '{category}'",
                path="metadata.category",
                hint=f"Known categories: {', '.join(sorted(KNOWN_CATEGORIES))}",
                severity="warning",
            )
        return None

    def _validate_content_ref(self, content: ContentRef) -> Optional[ValidationIssue]:
        if content.path and content.inline:
            return ValidationIssue(
                code="VAL-CONTENT-BOTH",
                message="Content has both 'path' and 'inline' set",
                path="spec.content",
                hint="Use either 'path' or 'inline', not both",
            )
        if not content.path and not content.inline:
            return ValidationIssue(
                code="VAL-CONTENT-EMPTY",
                message="Content has neither 'path' nor 'inline' set",
                path="spec.content",
                hint="Set either spec.content.path or spec.content.inline",
            )
        return None
