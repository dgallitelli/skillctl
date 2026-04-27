"""Diff two skill versions from the local store."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field

import yaml

from skillctl.store import ContentStore
from skillctl.utils import parse_ref as _parse_ref


@dataclass
class DiffResult:
    """Structured result of comparing two skill versions."""

    ref_a: str
    ref_b: str
    metadata_changes: dict[str, tuple] = field(default_factory=dict)
    breaking_changes: list[str] = field(default_factory=list)
    content_diff: str = ""

    def to_dict(self) -> dict:
        return {
            "ref_a": self.ref_a,
            "ref_b": self.ref_b,
            "metadata_changes": {
                k: {"old": v[0], "new": v[1]}
                for k, v in self.metadata_changes.items()
            },
            "breaking_changes": self.breaking_changes,
            "content_diff": self.content_diff,
        }


def _load_manifest(store: ContentStore, content_hash: str) -> dict:
    """Load the .manifest.yaml for a given content hash."""
    prefix = content_hash[:2]
    manifest_path = store.store_dir / prefix / f"{content_hash}.manifest.yaml"
    if not manifest_path.exists():
        return {}
    with open(manifest_path) as f:
        return yaml.safe_load(f) or {}


def diff_skills(store: ContentStore, ref_a: str, ref_b: str) -> DiffResult:
    """Compare two skill versions and return a structured diff result."""
    name_a, ver_a = _parse_ref(ref_a)
    name_b, ver_b = _parse_ref(ref_b)

    # Pull both versions
    content_a, entry_a = store.pull(name_a, ver_a)
    content_b, entry_b = store.pull(name_b, ver_b)

    # Load manifests
    manifest_a = _load_manifest(store, entry_a["hash"])
    manifest_b = _load_manifest(store, entry_b["hash"])

    result = DiffResult(ref_a=ref_a, ref_b=ref_b)

    # Compare metadata
    meta_a = manifest_a.get("metadata", {})
    meta_b = manifest_b.get("metadata", {})
    for key in sorted(set(list(meta_a.keys()) + list(meta_b.keys()))):
        val_a = meta_a.get(key)
        val_b = meta_b.get(key)
        if val_a != val_b:
            result.metadata_changes[key] = (val_a, val_b)

    # Compare spec for breaking changes
    spec_a = manifest_a.get("spec", {})
    spec_b = manifest_b.get("spec", {})

    # Check parameters
    params_a = {p["name"]: p for p in spec_a.get("parameters", [])} if spec_a.get("parameters") else {}
    params_b = {p["name"]: p for p in spec_b.get("parameters", [])} if spec_b.get("parameters") else {}

    for pname in params_a:
        if pname not in params_b:
            result.breaking_changes.append(f"Parameter '{pname}' removed")
        else:
            old_type = params_a[pname].get("type")
            new_type = params_b[pname].get("type")
            if old_type != new_type:
                result.breaking_changes.append(
                    f"Parameter '{pname}' type changed: {old_type} → {new_type}"
                )

    # Check capabilities
    caps_a = set(spec_a.get("capabilities", []))
    caps_b = set(spec_b.get("capabilities", []))
    for cap in sorted(caps_a - caps_b):
        result.breaking_changes.append(f"Capability '{cap}' removed")

    # Content diff
    text_a = content_a.decode("utf-8", errors="replace").splitlines(keepends=True)
    text_b = content_b.decode("utf-8", errors="replace").splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        text_a, text_b,
        fromfile=ref_a,
        tofile=ref_b,
    ))
    result.content_diff = "".join(diff_lines)

    return result


def format_diff(result: DiffResult) -> str:
    """Format a DiffResult for human-readable terminal output."""
    lines: list[str] = []
    lines.append(f"Comparing {result.ref_a} → {result.ref_b}")
    lines.append("")

    if result.metadata_changes:
        lines.append("Metadata changes:")
        for key, (old, new) in result.metadata_changes.items():
            old_str = json.dumps(old) if isinstance(old, (list, dict)) else repr(old) if isinstance(old, str) else str(old)
            new_str = json.dumps(new) if isinstance(new, (list, dict)) else repr(new) if isinstance(new, str) else str(new)
            lines.append(f"  {key}: {old_str} → {new_str}")
        lines.append("")

    if result.breaking_changes:
        lines.append("⚠ Breaking changes:")
        for change in result.breaking_changes:
            lines.append(f"  - {change}")
        lines.append("")

    if result.content_diff:
        lines.append("Content diff:")
        lines.append(result.content_diff)

    if not result.metadata_changes and not result.breaking_changes and not result.content_diff:
        lines.append("No differences found.")

    return "\n".join(lines)
