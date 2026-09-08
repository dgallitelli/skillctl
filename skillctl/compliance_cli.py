"""CLI commands for compliance mapping.

``skillctl compliance {frameworks,classify,report,attest,gaps}``.
Reads evidence from local SkillsOps stores under ``~/.skillctl``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from skillctl.errors import SkillctlError
from skillctl.experimental import warn_experimental

_HOME = Path.home() / ".skillctl"
_AUDIT_LOG = _HOME / "registry" / "audit.jsonl"
_REGISTRY_DB = _HOME / "registry" / "registry.db"
_ATTEST_DB = _HOME / "attestations.db"
_DEPLOY_DB = _HOME / "deployments.db"


def _attestation_store():
    from skillctl.compliance.attestation import AttestationStore

    _HOME.mkdir(parents=True, exist_ok=True)
    store = AttestationStore(_ATTEST_DB)
    store.initialize()
    return store


def _deployment_store():
    from skillctl.deployment.store import DeploymentStore

    if not _DEPLOY_DB.exists():
        return None
    store = DeploymentStore(_DEPLOY_DB)
    store.initialize()
    return store


def _rbac_store():
    if not _REGISTRY_DB.exists():
        return None
    try:
        from skillctl.registry.db import MetadataDB
        from skillctl.registry.rbac.store import RBACStore

        db = MetadataDB(_REGISTRY_DB, check_same_thread=False)
        db.initialize()
        store = RBACStore(db.conn)
        store.initialize()
        return store
    except Exception:  # noqa: BLE001
        return None


def _build_collector(skill_path: str | None):
    from skillctl.compliance.evidence import EvidenceCollector

    audit_hmac_key = None
    env_key = os.environ.get("SKILLCTL_HMAC_KEY")
    key_path = _HOME / "registry" / "hmac.key"
    if env_key:
        audit_hmac_key = env_key.encode()
    elif key_path.is_file():
        audit_hmac_key = key_path.read_bytes()

    return EvidenceCollector(
        skill_path=skill_path,
        audit_log_path=str(_AUDIT_LOG) if _AUDIT_LOG.exists() else None,
        rbac_store=_rbac_store(),
        attestation_store=_attestation_store(),
        deployment_store=_deployment_store(),
        audit_hmac_key=audit_hmac_key,
    )


def _classify_skill(skill_path: str | None):
    from skillctl.compliance.classification import RiskClassifier
    from skillctl.manifest import ManifestLoader

    metadata = {}
    if skill_path:
        try:
            manifest, _ = ManifestLoader().load(skill_path)
            metadata = {
                "name": manifest.metadata.name,
                "version": manifest.metadata.version,
                "description": manifest.metadata.description,
                "category": manifest.metadata.category,
                "tags": manifest.metadata.tags,
            }
        except Exception:  # noqa: BLE001
            pass
    return RiskClassifier().classify(metadata), metadata


# ---------------------------------------------------------------------------


def cmd_compliance_frameworks(args) -> int:
    from skillctl.compliance.frameworks import load_builtin_frameworks

    fws = load_builtin_frameworks()
    print(f"{'ID':<14} {'NAME':<28} {'VERSION':<12} {'EFFECTIVE':<12} CONTROLS")
    for fw in fws.values():
        print(f"{fw.id:<14} {fw.name:<28} {fw.version:<12} {fw.effective_date or '—':<12} {len(fw.all_controls)}")
    return 0


def cmd_compliance_classify(args) -> int:
    rc, _ = _classify_skill(args.skill)
    print(f"{rc.skill_name or args.skill} classified as {rc.risk_level.value.upper()} risk")
    print(f"  Reason: {rc.classification_reason}")
    return 0


def cmd_compliance_report(args) -> int:
    from skillctl.compliance.frameworks import load_builtin_frameworks
    from skillctl.compliance.report import ComplianceReportGenerator

    fws = load_builtin_frameworks()
    if args.framework not in fws:
        raise SkillctlError(
            code="E_UNKNOWN_FRAMEWORK",
            what=f"Unknown framework '{args.framework}'",
            why=f"Available: {', '.join(fws)}",
            fix="Run 'skillctl compliance frameworks' to list options.",
        )
    rc, metadata = _classify_skill(args.skill)
    gen = ComplianceReportGenerator(_build_collector(args.skill), fws)
    report = gen.generate(
        skill_name=rc.skill_name or (args.skill or ""),
        skill_version=rc.skill_version or "0.0.0",
        framework_id=args.framework,
        risk_classification=rc,
    )

    if args.format == "json":
        print(json.dumps(gen.to_json(report), indent=2))
    else:
        print(gen.to_markdown(report))
    if report.risk_classification and report.risk_classification.risk_level.value == "unacceptable":
        return 2
    # A preview is clean only when every applicable control is fully
    # satisfied. Partial and pending results are not approval.
    return 0 if not report.gaps else 1


def cmd_compliance_gaps(args) -> int:
    from skillctl.compliance.frameworks import load_builtin_frameworks
    from skillctl.compliance.report import ComplianceReportGenerator

    fws = load_builtin_frameworks()
    rc, _ = _classify_skill(args.skill)
    gen = ComplianceReportGenerator(_build_collector(args.skill), fws)
    report = gen.generate(
        skill_name=rc.skill_name or (args.skill or ""),
        skill_version=rc.skill_version or "0.0.0",
        framework_id=args.framework,
        risk_classification=rc,
    )
    if not report.gaps:
        print("No mapped gaps found in this preview.")
        return 0
    print(f"Mapped gaps for {report.skill_name} against {report.framework_name} (preview):")
    for a in report.gaps:
        icon = "⏳" if a.status.value == "pending" else "✗"
        print(f"  {icon} {a.control.id} — {a.control.name} [{a.status.value}]")
        print(f"      {a.gap_description}")
        if a.recommendation:
            print(f"      → {a.recommendation}")
    return 1


def cmd_compliance_attest(args) -> int:
    from skillctl.compliance.frameworks import load_builtin_frameworks

    frameworks = load_builtin_frameworks()
    framework = frameworks.get(args.framework)
    if framework is None:
        raise SkillctlError(
            code="E_UNKNOWN_FRAMEWORK",
            what=f"Unknown framework '{args.framework}'",
            why=f"Available: {', '.join(frameworks)}",
            fix="Run 'skillctl compliance frameworks' to list options.",
        )
    control = next((item for item in framework.all_controls if item.id == args.control), None)
    if control is None:
        raise SkillctlError(
            code="E_UNKNOWN_CONTROL",
            what=f"Unknown control '{args.control}' in framework '{args.framework}'",
            why="Attestations must reference a declared framework control",
            fix="Inspect the framework report and use one of its control IDs.",
        )
    if not control.human_review_required:
        raise SkillctlError(
            code="E_ATTESTATION_NOT_REQUIRED",
            what=f"Control '{args.control}' does not accept manual attestation",
            why="This control is evaluated from automated evidence",
            fix="Supply the automated evidence recommended by the control mapping.",
        )

    rc, _ = _classify_skill(args.skill)
    store = _attestation_store()
    att = store.add(
        control_id=args.control,
        skill_name=rc.skill_name or (args.skill or ""),
        skill_version=rc.skill_version or "0.0.0",
        framework_id=args.framework,
        attested_by=args.by or "cli-user",
        statement=args.statement,
        evidence_description=args.evidence or "",
    )
    print(f"⚠ Local attestation recorded for {args.control} (id: {att.id[:8]}, valid until {att.valid_until[:10]}).")
    print("  Attester identity is unverified; this record cannot satisfy an enforcement gate.")
    return 0


def register_compliance_commands(sub) -> None:
    cp = sub.add_parser(
        "compliance",
        help="[experimental] Preview non-certifying governance-control mappings",
    )
    csub = cp.add_subparsers(dest="compliance_command")

    csub.add_parser("frameworks", help="List available compliance frameworks")

    cls = csub.add_parser("classify", help="Classify a skill's risk level")
    cls.add_argument("skill", help="Path to skill directory/manifest")
    cls.add_argument("--interactive", action="store_true")

    rep = csub.add_parser("report", help="Generate a non-certifying control-mapping preview")
    rep.add_argument("skill", help="Path to skill directory/manifest")
    rep.add_argument("--framework", default="eu-ai-act")
    rep.add_argument("--format", choices=["md", "json"], default="md")

    gaps = csub.add_parser("gaps", help="Show unsatisfied, partial, and pending mapped controls")
    gaps.add_argument("skill")
    gaps.add_argument("--framework", default="eu-ai-act")

    att = csub.add_parser("attest", help="Record an unverified local human attestation")
    att.add_argument("--control", required=True)
    att.add_argument("--skill", required=True)
    att.add_argument("--statement", required=True)
    att.add_argument("--framework", default="eu-ai-act")
    att.add_argument("--evidence", default="")
    att.add_argument("--by", default=None)


_DISPATCH = {
    "frameworks": cmd_compliance_frameworks,
    "classify": cmd_compliance_classify,
    "report": cmd_compliance_report,
    "gaps": cmd_compliance_gaps,
    "attest": cmd_compliance_attest,
}


def dispatch_compliance(args) -> int:
    handler = _DISPATCH.get(args.compliance_command)
    if handler is None:
        print("Usage: skillctl compliance {frameworks|classify|report|gaps|attest}", file=sys.stderr)
        return 1
    warn_experimental(
        "compliance mapping",
        "Reports are non-certifying previews and cannot authorize promotion or establish legal compliance.",
    )
    return handler(args)
