"""CLI commands for progressive deployment.

``skillctl deploy {canary,blue-green,staged,status,promote,rollback,history}``.
Deployments are tracked in a local SQLite store under ``~/.skillctl``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from skillctl.errors import SkillctlError

_DEPLOY_DB = Path.home() / ".skillctl" / "deployments.db"


def _engine():
    from skillctl.deployment.engine import DeploymentEngine
    from skillctl.deployment.store import DeploymentStore

    _DEPLOY_DB.parent.mkdir(parents=True, exist_ok=True)
    store = DeploymentStore(_DEPLOY_DB)
    store.initialize()
    return DeploymentEngine(store), store


def _parse_stages(value: str | None) -> list[float]:
    if not value:
        return [0.01, 0.05, 0.25, 0.50, 1.0]
    out = []
    for part in value.split(","):
        part = part.strip().rstrip("%")
        if not part:
            continue
        num = float(part)
        out.append(num / 100.0 if num > 1 else num)
    return out


def cmd_deploy_canary(args) -> int:
    from skillctl.deployment.models import DeploymentStrategy

    engine, _ = _engine()
    dep = engine.create(
        skill_name=args.skill,
        skill_namespace=args.namespace,
        to_version=args.version,
        from_version=args.from_version,
        strategy=DeploymentStrategy.CANARY,
        config={"stages": _parse_stages(args.stages), "auto_rollback": args.auto_rollback, "auto_promote": False},
        initiated_by=args.by or "cli-user",
    )
    stages = dep.config["stages"]
    pct = " → ".join(f"{int(s * 100)}%" for s in stages)
    print(f"Deployment {dep.id} created")
    print(f"Strategy: canary ({pct})")
    print(f"Auto-rollback: {'enabled' if args.auto_rollback else 'disabled'}")
    print(f"Status: {dep.state.value} (stage 1/{len(stages)}, traffic: {int(dep.current_traffic_percent * 100)}%)")
    return 0


def cmd_deploy_blue_green(args) -> int:
    from skillctl.deployment.models import DeploymentStrategy

    engine, _ = _engine()
    dep = engine.create(
        skill_name=args.skill,
        skill_namespace=args.namespace,
        to_version=args.version,
        from_version=args.from_version,
        strategy=DeploymentStrategy.BLUE_GREEN,
        config={"auto_rollback": args.auto_rollback},
        initiated_by=args.by or "cli-user",
    )
    print(f"Deployment {dep.id} created")
    print("Strategy: blue-green (blue=current serving; green warming)")
    print(f"Status: {dep.state.value} — promote to switch traffic to {args.version}")
    return 0


def cmd_deploy_staged(args) -> int:
    from skillctl.deployment.models import DeploymentStrategy

    engine, _ = _engine()
    names = [s.strip() for s in (args.stages or "dev,staging,prod").split(",")]
    approval = set((args.require_approval or "").split(",")) if args.require_approval else set()
    stages = [
        {"name": n, "traffic_percent": 1.0 if i == len(names) - 1 else 0.5, "auto_promote": n not in approval}
        for i, n in enumerate(names)
    ]
    dep = engine.create(
        skill_name=args.skill,
        skill_namespace=args.namespace,
        to_version=args.version,
        from_version=args.from_version,
        strategy=DeploymentStrategy.STAGED,
        config={"stages": stages, "auto_rollback": args.auto_rollback},
        initiated_by=args.by or "cli-user",
    )
    print(f"Deployment {dep.id} created")
    print(f"Strategy: staged ({' → '.join(names)})")
    print(f"Status: {dep.state.value} (stage 1/{len(names)}: {names[0]})")
    return 0


def cmd_deploy_status(args) -> int:
    _, store = _engine()
    deployments = store.list_all()
    if args.deployment_id:
        deployments = [d for d in deployments if d.id == args.deployment_id]
    if args.skill:
        deployments = [d for d in deployments if d.skill_name == args.skill]
    if not deployments:
        print("No deployments found.")
        return 0
    print(f"{'DEPLOYMENT':<14} {'SKILL':<20} {'VERSION':<9} {'STRATEGY':<11} {'STATE':<13} TRAFFIC")
    for d in deployments:
        print(
            f"{d.id:<14} {d.skill_name:<20} {d.to_version:<9} {d.strategy.value:<11} "
            f"{d.state.value:<13} {int(d.current_traffic_percent * 100)}%"
        )
    return 0


def cmd_deploy_promote(args) -> int:
    from skillctl.deployment.engine import DeploymentError

    engine, _ = _engine()
    try:
        dep = engine.promote(args.deployment_id, approved_by=args.by or "cli-user")
    except DeploymentError as e:
        raise SkillctlError(code="E_DEPLOY", what="Promotion failed", why=str(e), fix="Check deploy status.") from e
    print(
        f"Promoted {dep.id} → stage {dep.current_stage + 1} ({int(dep.current_traffic_percent * 100)}% traffic, {dep.state.value})"
    )
    return 0


def cmd_deploy_rollback(args) -> int:
    from skillctl.deployment.engine import DeploymentError

    engine, _ = _engine()
    try:
        dep = engine.rollback(args.deployment_id, reason=args.reason or "manual", rolled_back_by=args.by or "cli-user")
    except DeploymentError as e:
        raise SkillctlError(code="E_DEPLOY", what="Rollback failed", why=str(e), fix="Check deploy status.") from e
    print(f"Rolled back {dep.id}: {dep.skill_name}@{dep.to_version} → {dep.from_version or '(none)'}")
    return 0


def cmd_deploy_history(args) -> int:
    _, store = _engine()
    deployments = store.list_all()
    if args.skill:
        deployments = [d for d in deployments if d.skill_name == args.skill]
    if not deployments:
        print("No deployment history.")
        return 0
    print(f"{'DEPLOYMENT':<14} {'SKILL':<18} {'FROM':<8} {'TO':<8} {'STRATEGY':<11} RESULT")
    for d in deployments:
        print(
            f"{d.id:<14} {d.skill_name:<18} {str(d.from_version or '-'):<8} {d.to_version:<8} "
            f"{d.strategy.value:<11} {d.state.value}"
        )
    return 0


def register_deploy_commands(sub) -> None:
    dp = sub.add_parser("deploy", help="Progressive skill deployment (canary/blue-green/staged)")
    dsub = dp.add_subparsers(dest="deploy_command")

    def _common(p):
        p.add_argument("skill", help="Skill name (namespace/skill)")
        p.add_argument("--version", required=True)
        p.add_argument("--namespace", required=True)
        p.add_argument("--from", dest="from_version", default=None, help="Previous version")
        p.add_argument("--by", default=None)

    can = dsub.add_parser("canary", help="Start a canary deployment")
    _common(can)
    can.add_argument("--stages", default=None, help='Comma list, e.g. "1,5,25,50,100"')
    can.add_argument("--stage-duration", default=None, help="(informational)")
    can.add_argument("--auto-rollback", action="store_true")

    bg = dsub.add_parser("blue-green", help="Start a blue-green deployment")
    _common(bg)
    bg.add_argument("--warmup", default=None)
    bg.add_argument("--parallel", default=None)
    bg.add_argument("--auto-rollback", action="store_true")

    stg = dsub.add_parser("staged", help="Start a staged deployment with gates")
    _common(stg)
    stg.add_argument("--stages", default="dev,staging,prod")
    stg.add_argument("--require-approval", default=None, help="Comma list of stages needing approval")
    stg.add_argument("--auto-rollback", action="store_true")

    st = dsub.add_parser("status", help="Show deployment status")
    st.add_argument("--deployment-id", default=None)
    st.add_argument("--skill", default=None)

    pr = dsub.add_parser("promote", help="Promote a deployment to the next stage")
    pr.add_argument("deployment_id")
    pr.add_argument("--by", default=None)
    pr.add_argument("--force", action="store_true")

    rb = dsub.add_parser("rollback", help="Roll back a deployment")
    rb.add_argument("deployment_id")
    rb.add_argument("--reason", default=None)
    rb.add_argument("--by", default=None)

    hist = dsub.add_parser("history", help="Show deployment history")
    hist.add_argument("--skill", default=None)
    hist.add_argument("--since", default=None)


_DISPATCH = {
    "canary": cmd_deploy_canary,
    "blue-green": cmd_deploy_blue_green,
    "staged": cmd_deploy_staged,
    "status": cmd_deploy_status,
    "promote": cmd_deploy_promote,
    "rollback": cmd_deploy_rollback,
    "history": cmd_deploy_history,
}


def dispatch_deploy(args) -> int:
    handler = _DISPATCH.get(args.deploy_command)
    if handler is None:
        print("Usage: skillctl deploy {canary|blue-green|staged|status|promote|rollback|history}", file=sys.stderr)
        return 1
    return handler(args)
