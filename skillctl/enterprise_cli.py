"""CLI commands for Milestone 4 enterprise features.

``skillctl ci {list,init}``, ``skillctl forensics {provenance,who-accessed,invocations}``,
and ``skillctl identity inspect``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from skillctl.errors import SkillctlError
from skillctl.experimental import warn_experimental

_LINEAGE_DB = Path.home() / ".skillctl" / "lineage.db"
_AUDIT_LOG = Path.home() / ".skillctl" / "registry" / "audit.jsonl"


# ---------------------------------------------------------------------------
# ci
# ---------------------------------------------------------------------------


def cmd_ci_list(args) -> int:
    from skillctl.cicd import list_systems

    print("Available CI templates:")
    for s in list_systems():
        print(f"  {s}")
    return 0


def cmd_ci_init(args) -> int:
    from skillctl.cicd import write_template

    try:
        path = write_template(args.system, args.output)
    except ValueError as e:
        raise SkillctlError(
            code="E_CI", what="Unknown CI system", why=str(e), fix="Use github, gitlab, or jenkins."
        ) from e
    print(f"✓ Wrote {args.system} governance pipeline to {path}")
    return 0


# ---------------------------------------------------------------------------
# forensics
# ---------------------------------------------------------------------------


def _lineage():
    from skillctl.lineage.store import LineageStore

    if not _LINEAGE_DB.exists():
        raise SkillctlError(
            code="E_NO_LINEAGE",
            what="No lineage database found",
            why=f"Expected {_LINEAGE_DB}",
            fix="Record lineage during skill invocation, or pass --lineage-db.",
        )
    store = LineageStore(_LINEAGE_DB)
    store.initialize()
    return store


def _lineage_at(path):
    from skillctl.lineage.store import LineageStore

    store = LineageStore(path)
    store.initialize()
    return store


def cmd_forensics_provenance(args) -> int:
    store = _lineage_at(args.lineage_db) if args.lineage_db else _lineage()
    from skillctl.forensics.query import ForensicQuery

    q = ForensicQuery(store)
    result = q.provenance(args.data)
    print(f"Provenance of {args.data}:")
    if not result["sources"]:
        print("  (no upstream sources recorded)")
    for s in result["sources"]:
        print(f"  ← {s}")
    return 0


def cmd_forensics_who_accessed(args) -> int:
    store = _lineage_at(args.lineage_db) if args.lineage_db else _lineage()
    from skillctl.forensics.query import ForensicQuery

    q = ForensicQuery(store)
    actors = q.who_accessed(args.data, since=args.since, until=args.until)
    print(f"Actors who accessed {args.data}: {', '.join(actors) or '(none)'}")
    return 0


def cmd_forensics_invocations(args) -> int:
    store = _lineage_at(args.lineage_db) if args.lineage_db else _lineage()
    from skillctl.forensics.query import ForensicQuery

    q = ForensicQuery(store)
    invs = q.invocations_accessing(skill=args.skill, label=args.label, since=args.since, until=args.until)
    if not invs:
        print("No matching invocations.")
        return 0
    print(f"{'INVOCATION':<38} {'SKILL':<20} ACTOR")
    for i in invs:
        print(f"{i['invocation_id']:<38} {i['skill']:<20} {i['actor']}")
    return 0


# ---------------------------------------------------------------------------
# identity inspect
# ---------------------------------------------------------------------------


def cmd_identity_inspect(args) -> int:
    from skillctl.identity.models import GroupRoleMapping, IdentityProviderConfig, IdentityProviderType
    from skillctl.identity.resolver import IdentityError, IdentityResolver

    mappings = []
    for spec in args.group_map or []:
        # format: group=role:namespace
        group, _, role_ns = spec.partition("=")
        role, _, ns = role_ns.partition(":")
        mappings.append(GroupRoleMapping(idp_group=group, skillsops_role=role, namespace_pattern=ns or "*"))

    config = IdentityProviderConfig(
        id=args.provider or "cli",
        type=IdentityProviderType.OIDC,
        oidc_issuer_url=args.issuer,
        oidc_audience=args.audience,
        group_mappings=mappings,
    )
    resolver = IdentityResolver(config, secret=args.secret)
    try:
        identity = resolver.resolve(args.token)
    except IdentityError as e:
        print(f"✗ Token rejected: {e}", file=sys.stderr)
        return 1
    print(f"Subject:  {identity.subject}")
    print(f"Email:    {identity.email}")
    print(f"Groups:   {', '.join(identity.groups) or '(none)'}")
    print(f"Roles:    {', '.join(identity.resolved_roles) or '(none)'}")
    return 0


# ---------------------------------------------------------------------------
# registration + dispatch
# ---------------------------------------------------------------------------


def register_enterprise_commands(sub) -> None:
    ci = sub.add_parser("ci", help="[preview] Generate CI/CD starter templates")
    ci_sub = ci.add_subparsers(dest="ci_command")
    ci_sub.add_parser("list", help="List available CI templates")
    init = ci_sub.add_parser("init", help="Write a governance pipeline template")
    init.add_argument("--system", required=True, choices=["github", "gitlab", "jenkins"])
    init.add_argument("--output", default=None, help="Output path (default: system convention)")

    fx = sub.add_parser(
        "forensics",
        help="[experimental] Query caller-recorded local lineage",
    )
    fx_sub = fx.add_subparsers(dest="forensics_command")
    prov = fx_sub.add_parser("provenance", help="Trace a data item back to its sources")
    prov.add_argument("--data", required=True)
    prov.add_argument("--lineage-db", default=None)
    wa = fx_sub.add_parser("who-accessed", help="List actors who accessed a data item")
    wa.add_argument("--data", required=True)
    wa.add_argument("--since", default=None)
    wa.add_argument("--until", default=None)
    wa.add_argument("--lineage-db", default=None)
    inv = fx_sub.add_parser("invocations", help="List invocations matching skill/label/window")
    inv.add_argument("--skill", default=None)
    inv.add_argument("--label", default=None)
    inv.add_argument("--since", default=None)
    inv.add_argument("--until", default=None)
    inv.add_argument("--lineage-db", default=None)

    idy = sub.add_parser(
        "identity",
        help="[experimental] Inspect locally signed HS256 identity tokens",
    )
    idy_sub = idy.add_subparsers(dest="identity_command")
    insp = idy_sub.add_parser(
        "inspect",
        help="Validate a locally signed HS256 JWT and show mapped roles",
    )
    insp.add_argument("--token", required=True)
    insp.add_argument("--secret", required=True)
    insp.add_argument("--issuer", default=None)
    insp.add_argument("--audience", default=None)
    insp.add_argument("--provider", default=None)
    insp.add_argument("--group-map", action="append", help="group=role:namespace (repeatable)")


def dispatch_ci(args) -> int:
    handler = {"list": cmd_ci_list, "init": cmd_ci_init}.get(args.ci_command)
    if handler is None:
        print("Usage: skillctl ci {list|init}", file=sys.stderr)
        return 1
    warn_experimental(
        "CI/CD templates",
        "Generated files are starter templates; pin dependencies and review permissions before use.",
    )
    return handler(args)


def dispatch_forensics(args) -> int:
    handler = {
        "provenance": cmd_forensics_provenance,
        "who-accessed": cmd_forensics_who_accessed,
        "invocations": cmd_forensics_invocations,
    }.get(args.forensics_command)
    if handler is None:
        print("Usage: skillctl forensics {provenance|who-accessed|invocations}", file=sys.stderr)
        return 1
    warn_experimental(
        "lineage forensics",
        "Results include only data explicitly recorded by callers and are not an exhaustive audit record.",
    )
    return handler(args)


def dispatch_identity(args) -> int:
    handler = {"inspect": cmd_identity_inspect}.get(args.identity_command)
    if handler is None:
        print("Usage: skillctl identity inspect --token <jwt> --secret <s>", file=sys.stderr)
        return 1
    warn_experimental(
        "identity inspection",
        "This HS256 utility is not connected to registry authentication and does not implement IdP JWKS validation.",
    )
    return handler(args)
