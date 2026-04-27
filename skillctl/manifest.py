"""SkillManifest dataclasses and ManifestLoader for skill.yaml / SKILL.md files."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from skillctl.errors import SkillctlError

KNOWN_CAPABILITIES = {"read_file", "read_code", "write_file", "network_access", "exec"}


@dataclass
class Author:
    name: str
    email: Optional[str] = None


@dataclass
class ContentRef:
    path: Optional[str] = None
    inline: Optional[str] = None


@dataclass
class Parameter:
    name: str
    type: str  # "string" | "number" | "boolean" | "enum"
    required: bool = False
    default: Optional[str] = None
    description: Optional[str] = None
    values: list[str] = field(default_factory=list)


@dataclass
class Dependency:
    name: str
    version: str


@dataclass
class SkillSpec:
    content: ContentRef = field(default_factory=ContentRef)
    parameters: list[Parameter] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)


@dataclass
class SkillMetadata:
    name: str = ""
    version: str = "0.0.0"
    description: str = ""
    authors: list[Author] = field(default_factory=list)
    license: Optional[str] = None
    tags: list[str] = field(default_factory=list)


@dataclass
class SkillManifest:
    api_version: str = "skillctl.io/v1"
    kind: str = "Skill"
    metadata: SkillMetadata = field(default_factory=SkillMetadata)
    spec: SkillSpec = field(default_factory=SkillSpec)
    governance: Optional[dict] = None

    def to_dict(self) -> dict:
        """Serialize to a dict suitable for JSON/YAML output."""
        d: dict = {
            "apiVersion": self.api_version,
            "kind": self.kind,
            "metadata": {
                "name": self.metadata.name,
                "version": self.metadata.version,
                "description": self.metadata.description,
                **(
                    {
                        "authors": [
                            {"name": a.name, **({"email": a.email} if a.email else {})} for a in self.metadata.authors
                        ]
                    }
                    if self.metadata.authors
                    else {}
                ),
                **({"license": self.metadata.license} if self.metadata.license else {}),
                **({"tags": self.metadata.tags} if self.metadata.tags else {}),
            },
            "spec": {
                "content": {
                    **({"path": self.spec.content.path} if self.spec.content.path else {}),
                    **({"inline": self.spec.content.inline} if self.spec.content.inline else {}),
                },
                **(
                    {
                        "parameters": [
                            {
                                "name": p.name,
                                "type": p.type,
                                **({"required": p.required} if p.required else {}),
                                **({"default": p.default} if p.default else {}),
                                **({"description": p.description} if p.description else {}),
                                **({"values": p.values} if p.values else {}),
                            }
                            for p in self.spec.parameters
                        ]
                    }
                    if self.spec.parameters
                    else {}
                ),
                "capabilities": list(self.spec.capabilities),
                **(
                    {"dependencies": [{"name": d.name, "version": d.version} for d in self.spec.dependencies]}
                    if self.spec.dependencies
                    else {}
                ),
            },
            **({"governance": self.governance} if self.governance else {}),
        }
        return d


@dataclass
class Warning:
    code: str
    message: str
    hint: str


class ManifestLoader:
    """Loads skill.yaml or auto-wraps plain SKILL.md files."""

    def load(self, path: str) -> tuple[SkillManifest, list[Warning]]:
        """Load a manifest from a file path.

        If path is a .md file, auto-wraps in minimal manifest.
        If path is a directory, looks for skill.yaml then SKILL.md.
        """
        p = Path(path)
        warnings: list[Warning] = []

        if p.is_dir():
            yaml_path = p / "skill.yaml"
            md_path = p / "SKILL.md"
            if yaml_path.exists():
                return self._load_yaml(yaml_path), warnings
            elif md_path.exists():
                manifest, warn = self._wrap_markdown(md_path)
                warnings.append(warn)
                return manifest, warnings
            else:
                raise SkillctlError(
                    code="E_NO_MANIFEST",
                    what=f"No skill.yaml or SKILL.md found in {path}",
                    why="A skill needs either a skill.yaml manifest or a SKILL.md file",
                    fix="Run 'skillctl init' to create a new skill",
                )
        elif p.suffix in (".yaml", ".yml"):
            return self._load_yaml(p), warnings
        elif p.suffix == ".md":
            manifest, warn = self._wrap_markdown(p)
            warnings.append(warn)
            return manifest, warnings
        else:
            raise SkillctlError(
                code="E_UNKNOWN_FORMAT",
                what=f"Unrecognized file type: {p.suffix}",
                why="skillctl expects .yaml, .yml, or .md files",
                fix="Provide a skill.yaml or SKILL.md file",
            )

    def _load_yaml(self, path: Path) -> SkillManifest:
        """Parse skill.yaml into SkillManifest."""
        with open(path) as f:
            raw = yaml.safe_load(f)
        if not raw or not isinstance(raw, dict):
            raise SkillctlError(
                code="E_INVALID_YAML",
                what=f"{path} is empty or not a YAML mapping",
                why="skill.yaml must contain a YAML document with at least metadata and spec keys",
                fix="Ensure the file is a valid YAML mapping, e.g. 'apiVersion: skillctl.io/v1'",
            )
        return self._dict_to_manifest(raw)

    def _wrap_markdown(self, path: Path) -> tuple[SkillManifest, Warning]:
        """Auto-wrap a plain SKILL.md in a minimal manifest."""
        content = path.read_text()
        name = path.parent.name or path.stem
        manifest = SkillManifest(
            metadata=SkillMetadata(name=name, version="0.0.0"),
            spec=SkillSpec(content=ContentRef(inline=content)),
        )
        warning = Warning(
            code="W_AUTO_WRAPPED",
            message=f"Auto-wrapped {path.name} in minimal manifest",
            hint="Run 'skillctl init' to generate a proper skill.yaml",
        )
        return manifest, warning

    def _dict_to_manifest(self, raw: dict) -> SkillManifest:
        """Convert parsed YAML dict to SkillManifest dataclass."""
        meta_raw = raw.get("metadata", {})
        spec_raw = raw.get("spec", {})
        content_raw = spec_raw.get("content", {})

        try:
            authors = [Author(**a) for a in meta_raw.get("authors", [])]
            params = [Parameter(**p) for p in spec_raw.get("parameters", [])]
            deps = [Dependency(**d) for d in spec_raw.get("dependencies", [])]
        except TypeError as exc:
            raise SkillctlError(
                code="E_MANIFEST_FIELDS",
                what=f"Unexpected fields in skill.yaml: {exc}",
                why="Each section in skill.yaml must only contain recognized fields",
                fix="Check the skill.yaml spec for allowed fields in authors, parameters, and dependencies",
            ) from exc
        content = ContentRef(
            path=content_raw.get("path"),
            inline=content_raw.get("inline"),
        )

        return SkillManifest(
            api_version=raw.get("apiVersion", "skillctl.io/v1"),
            kind=raw.get("kind", "Skill"),
            metadata=SkillMetadata(
                name=meta_raw.get("name", ""),
                version=meta_raw.get("version", "0.0.0"),
                description=meta_raw.get("description", ""),
                authors=authors,
                license=meta_raw.get("license"),
                tags=meta_raw.get("tags", []),
            ),
            spec=SkillSpec(
                content=content,
                parameters=params,
                capabilities=spec_raw.get("capabilities", []),
                dependencies=deps,
            ),
            governance=raw.get("governance"),
        )

    def resolve_content(self, manifest: SkillManifest, base_dir: str) -> str:
        """Resolve skill content from inline or path reference."""
        if manifest.spec.content.inline:
            return manifest.spec.content.inline
        if manifest.spec.content.path:
            base = Path(base_dir).resolve()
            content_path = (base / manifest.spec.content.path).resolve()
            if not str(content_path).startswith(str(base)):
                raise SkillctlError(
                    code="E_PATH_TRAVERSAL",
                    what=f"Content path escapes skill directory: {manifest.spec.content.path}",
                    why="spec.content.path must resolve to a file within the skill directory",
                    fix="Use a relative path like ./SKILL.md that stays inside the skill directory",
                )
            return content_path.read_text()
        return ""
