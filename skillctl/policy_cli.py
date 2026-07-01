"""CLI commands for runtime policy and observability.

``skillctl policy {list,validate,test,history}`` and
``skillctl observe {status,test}``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from skillctl.errors import SkillctlError

_DEFAULT_AUDIT_LOG = Path.home() / ".skillctl" / "registry" / "audit.jsonl"


def _load_cfg(args):
    from skillctl.policy.config import DEFAULT_CONFIG_PATH, PolicyConfigError, load_config_file

    path = getattr(args, "config", None) or DEFAULT_CONFIG_PATH
    try:
        return load_config_file(path)
    except PolicyConfigError as e:
        raise SkillctlError(
            code="E_POLICY_CONFIG",
            what="Could not load policy configuration",
            why=str(e),
            fix=f"Create {path} or pass --config <file>.",
        ) from e


# ---------------------------------------------------------------------------
# policy commands
# ---------------------------------------------------------------------------


def cmd_policy_list(args) -> int:
    from skillctl.policy.config import _HOOK_TYPES

    cfg = _load_cfg(args)
    if not cfg.policies:
        print("No policies configured.")
        return 0
    print(f"{'NAME':<20} {'TYPE':<24} {'PHASE':<7} STATUS")
    for p in cfg.policies:
        hook_cls, _ = _HOOK_TYPES[p.type]
        # phase is an instance property; read the class default cheaply
        try:
            phase = hook_cls.phase.fget(hook_cls.__new__(hook_cls))  # type: ignore[attr-defined]
        except Exception:
            phase = "both"
        print(f"{p.name:<20} {p.type:<24} {phase:<7} active")
    return 0


def cmd_policy_validate(args) -> int:
    from skillctl.policy.config import PolicyConfigError, build_engine

    cfg = _load_cfg(args)
    try:
        engine = build_engine(cfg)
    except PolicyConfigError as e:
        print(f"✗ Invalid policy config: {e}", file=sys.stderr)
        return 1
    print(f"✓ Policy config valid: {len(engine.hooks)} hook(s) registered")
    # Reachability check for external engines.
    for p in cfg.policies:
        if p.type == "external.opa":
            url = p.config.get("opa_url", "")
            print(
                f"  - {p.name}: OPA at {url} (reachability checked at runtime, fail_mode={p.config.get('fail_mode', 'closed')})"
            )
    return 0


def cmd_policy_test(args) -> int:
    from skillctl.manifest import ManifestLoader
    from skillctl.policy.config import build_engine
    from skillctl.policy.hooks import PolicyContext

    cfg = _load_cfg(args)
    engine = build_engine(cfg)

    # Build context from the skill manifest.
    skill_name, skill_version, skill_namespace, category = "", "", "", ""
    if args.skill:
        try:
            manifest, _ = ManifestLoader().load(args.skill)
            skill_name = manifest.metadata.name
            skill_version = manifest.metadata.version
            skill_namespace = skill_name.split("/")[0] if "/" in skill_name else skill_name
            category = manifest.metadata.category or ""
        except Exception as e:  # noqa: BLE001
            print(f"Warning: could not load skill manifest: {e}", file=sys.stderr)

    ts = args.at or datetime.now(timezone.utc).isoformat()
    ctx = PolicyContext(
        actor_id=args.as_user or "tester",
        actor_roles=(args.roles.split(",") if args.roles else []),
        actor_namespace=skill_namespace,
        skill_name=skill_name,
        skill_version=skill_version,
        skill_namespace=args.namespace or skill_namespace,
        skill_category=category,
        timestamp=ts,
    )

    result = asyncio.run(engine.evaluate_pre(ctx))
    print(f"Evaluating policies for: {skill_name or '(no skill)'}@{skill_version or '-'}")
    print(f"Actor: {ctx.actor_id} (roles: {', '.join(ctx.actor_roles) or 'none'})")
    print(f"Namespace: {ctx.skill_namespace or '-'}")
    print(f"Timestamp: {ts}")
    print()
    print("PRE-EXECUTION:")

    async def _per_hook():
        rows = []
        for hook in engine.hooks:
            if hook.phase in ("pre", "both"):
                r = await hook.evaluate_pre(ctx)
                rows.append(r)
        return rows

    for r in asyncio.run(_per_hook()):
        mark = "✓" if r.decision.value in ("allow", "warn") else "✗"
        print(f"  {mark} {r.hook_name}: {r.reason}")
    print()
    verdict = result.decision.value.upper()
    print(f"VERDICT: {verdict} ({result.reason})")
    return 0 if result.decision.value != "deny" else 1


def cmd_policy_history(args) -> int:
    audit_path = Path(getattr(args, "audit_log", None) or _DEFAULT_AUDIT_LOG)
    if not audit_path.is_file():
        print(f"No audit log found at {audit_path}.", file=sys.stderr)
        return 1
    rows = []
    for line in audit_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("action") != "policy_decision":
            continue
        if args.skill and args.skill not in entry.get("resource", ""):
            continue
        if args.actor and entry.get("actor") != args.actor:
            continue
        rows.append(entry)

    if not rows:
        print("No policy decisions found.")
        return 0
    print(f"{'TIMESTAMP':<28} {'ACTOR':<12} {'SKILL':<22} {'HOOK':<16} {'DECISION':<9} REASON")
    for e in rows[-200:]:
        d = e.get("details", {})
        print(
            f"{e.get('timestamp', '')[:27]:<28} {e.get('actor', ''):<12} "
            f"{e.get('resource', '')[:22]:<22} {str(d.get('hook_name', '')):<16} "
            f"{str(d.get('decision', '')):<9} {d.get('reason', '')}"
        )
    return 0


# ---------------------------------------------------------------------------
# observe commands
# ---------------------------------------------------------------------------


def cmd_observe_status(args) -> int:
    cfg_obs = {}
    try:
        cfg_obs = _load_cfg(args).observability
    except SkillctlError:
        pass

    try:
        import opentelemetry  # type: ignore[import-not-found]  # noqa: F401

        installed = True
    except ImportError:
        installed = False

    print(f"OpenTelemetry SDK installed: {'yes' if installed else 'no (pip install skillsops[observability])'}")
    if cfg_obs:
        print(f"Configured: enabled={cfg_obs.get('enabled', False)}")
        print(f"Exporter:   {cfg_obs.get('exporter', 'otlp')}")
        print(f"Endpoint:   {cfg_obs.get('endpoint', '(default)')}")
        print(f"Service:    {cfg_obs.get('service_name', 'skillsops')}")
    else:
        print("No observability config found (.skillctl/policies.yaml).")
    return 0


def cmd_observe_test(args) -> int:
    from skillctl.observability.tracer import configure_telemetry

    tracer = configure_telemetry(exporter="console")
    if not tracer.enabled:
        print("OpenTelemetry not installed — cannot send test span.", file=sys.stderr)
        print("Install with: pip install 'skillsops[observability]'", file=sys.stderr)
        return 1

    from skillctl.policy.hooks import PolicyContext

    async def _emit():
        ctx = PolicyContext(actor_id="observe-test", skill_name="test/span", skill_version="0.0.0")
        async with tracer.skill_invocation_span(ctx) as span:
            span.set_result(success=True, duration_ms=1.0)

    asyncio.run(_emit())
    print("✓ Test span emitted (console exporter — see output above)")
    return 0


# ---------------------------------------------------------------------------
# registration + dispatch
# ---------------------------------------------------------------------------


def register_policy_commands(sub) -> None:
    policy_p = sub.add_parser("policy", help="Inspect and test runtime policies")
    policy_sub = policy_p.add_subparsers(dest="policy_command")

    lst = policy_sub.add_parser("list", help="List configured policies")
    lst.add_argument("--config", default=None, help="Path to policies.yaml")

    val = policy_sub.add_parser("validate", help="Validate policy configuration")
    val.add_argument("--config", default=None)

    test = policy_sub.add_parser("test", help="Dry-run policy evaluation for a skill")
    test.add_argument("--skill", default=None, help="Path to skill directory/manifest")
    test.add_argument("--as", dest="as_user", default=None, help="Actor id to simulate")
    test.add_argument("--roles", default=None, help="Comma-separated actor roles")
    test.add_argument("--namespace", default=None, help="Override skill namespace")
    test.add_argument("--at", default=None, help="ISO timestamp to simulate")
    test.add_argument("--config", default=None)

    hist = policy_sub.add_parser("history", help="Show policy decision history from audit log")
    hist.add_argument("--skill", default=None)
    hist.add_argument("--actor", default=None)
    hist.add_argument("--audit-log", default=None, help="Path to audit.jsonl")

    observe_p = sub.add_parser("observe", help="Observability (OpenTelemetry) status")
    observe_sub = observe_p.add_subparsers(dest="observe_command")
    st = observe_sub.add_parser("status", help="Show OTel configuration/status")
    st.add_argument("--config", default=None)
    observe_sub.add_parser("test", help="Emit a test span")


_POLICY_DISPATCH = {
    "list": cmd_policy_list,
    "validate": cmd_policy_validate,
    "test": cmd_policy_test,
    "history": cmd_policy_history,
}
_OBSERVE_DISPATCH = {
    "status": cmd_observe_status,
    "test": cmd_observe_test,
}


def dispatch_policy(args) -> int:
    handler = _POLICY_DISPATCH.get(args.policy_command)
    if handler is None:
        print("Usage: skillctl policy {list|validate|test|history}", file=sys.stderr)
        return 1
    return handler(args)


def dispatch_observe(args) -> int:
    handler = _OBSERVE_DISPATCH.get(args.observe_command)
    if handler is None:
        print("Usage: skillctl observe {status|test}", file=sys.stderr)
        return 1
    return handler(args)
