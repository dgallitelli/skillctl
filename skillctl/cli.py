"""SkillsOps CLI — governance commands for agent skills.

Helpers (config IO, registry URL/token resolution, output discipline,
the registry HTTP request wrapper) live in
:mod:`skillctl._cli_helpers`.  They're re-imported here so the
``skillctl.cli.<helper>`` module-attribute path remains stable for
test monkeypatching.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from skillctl.config import (
    CONFIG_PATH,
    load_config as _load_skillctl_config,
    save_config as _save_skillctl_config,
    run_configure_wizard,
)
from skillctl.diff import diff_skills, format_diff
from skillctl.errors import SkillctlError
from skillctl.compliance_cli import dispatch_compliance, register_compliance_commands
from skillctl.deploy_cli import dispatch_deploy, register_deploy_commands
from skillctl.enterprise_cli import (
    dispatch_ci,
    dispatch_forensics,
    dispatch_identity,
    register_enterprise_commands,
)
from skillctl.manifest import ManifestLoader
from skillctl.policy_cli import (
    dispatch_observe,
    dispatch_policy,
    register_policy_commands,
)
from skillctl.rbac_cli import (
    dispatch_auth,
    dispatch_namespace,
    dispatch_rbac,
    register_rbac_commands,
)
from skillctl.store import ContentStore
from skillctl.utils import parse_ref as _parse_ref
from skillctl.validator import SchemaValidator
from skillctl.version import version_info

# Re-export helpers from _cli_helpers so tests that monkeypatch
# ``skillctl.cli.<helper>`` continue to work — the module-level
# rebindings here are what monkeypatch.setattr replaces, and the
# handlers below resolve the helper through the cli module's namespace
# at call time.
from skillctl._cli_helpers import (
    _emit_plugin_hint,
    _get_registry_token,
    _get_registry_url,
    _load_config,
    _load_github_token,
    _output_is_machine,
    _registry_request,
    _require_registry_url,
    _save_config,
)

__all__ = [
    "main",
    "cmd_apply",
    "cmd_validate",
    "cmd_create_skill",
    "cmd_doctor",
    "_get_registry_url",
    "_get_registry_token",
    "_require_registry_url",
    "_load_config",
    "_save_config",
    "_load_github_token",
    "_output_is_machine",
    "_emit_plugin_hint",
    "_registry_request",
    "ContentStore",
]


def main():
    _emit_plugin_hint()

    parser = argparse.ArgumentParser(
        prog="skillctl",
        description="Governance CLI for agent skills",
        epilog="Quick start: skillctl create skill my-org/my-skill && skillctl validate && skillctl eval audit .",
    )
    sub = parser.add_subparsers(dest="command")

    # -----------------------------------------------------------------------
    # PRIMARY COMMANDS (kubectl-style)
    # -----------------------------------------------------------------------

    # skillctl apply [path]
    apply_p = sub.add_parser("apply", help="Validate + push to local store (and remote if configured)")
    apply_p.add_argument("path", nargs="?", default=".", help="Path to skill directory or manifest")
    apply_p.add_argument("-f", dest="file", default=None, help="Path to skill (alias for positional path)")
    apply_p.add_argument("--dry-run", action="store_true", help="Show what would happen")
    apply_p.add_argument("--local", action="store_true", help="Skip remote publish, only push to local store")
    apply_p.add_argument("--registry-url", default=None, help="Registry URL (overrides config)")
    apply_p.add_argument("--token", default=None, help="Auth token (overrides config)")

    # skillctl create skill <name>
    create_p = sub.add_parser("create", help="Create a new resource")
    create_sub = create_p.add_subparsers(dest="create_resource")
    create_skill_p = create_sub.add_parser("skill", help="Scaffold a new skill (skill.yaml + SKILL.md)")
    create_skill_p.add_argument("name", help="Skill name (namespace/skill-name)")

    # skillctl get skills | skillctl get skill <ref>
    get_p = sub.add_parser("get", help="Get resources")
    get_sub = get_p.add_subparsers(dest="get_resource")

    get_skills_p = get_sub.add_parser("skills", help="List skills from local store (or remote with --remote)")
    get_skills_p.add_argument("--namespace", default=None, help="Filter by namespace")
    get_skills_p.add_argument("--tag", default=None, help="Filter by tag")
    get_skills_p.add_argument("--remote", action="store_true", help="List from remote registry")
    get_skills_p.add_argument("--query", default=None, help="Full-text search query (remote only)")
    get_skills_p.add_argument("--json", action="store_true", help="Output as JSON")
    get_skills_p.add_argument("--limit", type=int, default=20, help="Max results for remote (default: 20)")
    get_skills_p.add_argument("--registry-url", default=None, help="Registry URL (overrides config)")
    get_skills_p.add_argument("--token", default=None, help="Auth token (overrides config)")

    get_skill_p = get_sub.add_parser("skill", help="Pull/show a specific skill by name@version")
    get_skill_p.add_argument("ref", help="namespace/name@version")
    get_skill_p.add_argument("--remote", action="store_true", help="Pull from remote registry")
    get_skill_p.add_argument("--output", "-o", default=".", help="Output directory")
    get_skill_p.add_argument("--registry-url", default=None, help="Registry URL (overrides config)")
    get_skill_p.add_argument("--token", default=None, help="Auth token (overrides config)")

    get_installations_p = get_sub.add_parser("installations", help="List skills installed to IDEs")
    get_installations_p.add_argument("--target", default=None, help="Filter by IDE target")
    get_installations_p.add_argument("--json", action="store_true", help="Output as JSON")

    # skillctl describe skill <ref>
    describe_p = sub.add_parser("describe", help="Show detailed information about a resource")
    describe_sub = describe_p.add_subparsers(dest="describe_resource")
    describe_skill_p = describe_sub.add_parser("skill", help="Rich detail for a skill version")
    describe_skill_p.add_argument("ref", help="namespace/name@version")
    describe_skill_p.add_argument("--json", action="store_true", help="Output as JSON")

    # skillctl delete skill <ref>
    delete_p = sub.add_parser("delete", help="Delete a resource")
    delete_sub = delete_p.add_subparsers(dest="delete_resource")
    delete_skill_p = delete_sub.add_parser("skill", help="Remove a skill version from local store")
    delete_skill_p.add_argument("ref", help="namespace/name@version")
    delete_skill_p.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # skillctl logs <name>
    logs_p = sub.add_parser("logs", help="Show audit trail for a skill")
    logs_p.add_argument("name", help="Skill name (namespace/skill-name)")
    logs_p.add_argument("--registry-url", default=None, help="Registry URL (overrides config)")
    logs_p.add_argument("--token", default=None, help="Auth token (overrides config)")

    # -----------------------------------------------------------------------
    # EXISTING COMMANDS (kept as-is)
    # -----------------------------------------------------------------------

    # skillctl validate
    val_p = sub.add_parser("validate", help="Validate a skill manifest")
    val_p.add_argument("path", nargs="?", default=".", help="Path to skill.yaml or directory")
    val_p.add_argument("--json", action="store_true", help="Output as JSON (alias for --format=json)")
    val_p.add_argument(
        "--format",
        choices=["text", "json", "github"],
        default=None,
        help=(
            "Output format (default: text).  'github' emits Actions "
            "workflow commands so validation errors / warnings appear "
            "as inline PR annotations on skill.yaml / SKILL.md."
        ),
    )
    val_p.add_argument("--strict", action="store_true", help="Treat warnings as errors")

    # skillctl version
    sub.add_parser("version", help="Print version info")

    # skillctl diff
    diff_p = sub.add_parser("diff", help="Compare two skill versions")
    diff_p.add_argument("ref_a", help="First ref (namespace/name@version)")
    diff_p.add_argument("ref_b", help="Second ref (namespace/name@version)")
    diff_p.add_argument("--json", action="store_true", help="Output as JSON")

    # skillctl doctor
    sub.add_parser("doctor", help="Diagnose environment issues")

    # skillctl eval <subcommand>
    sub.add_parser(
        "eval", help="Evaluate skills (subcommands: audit, report, init, validate, snapshot, regression, lifecycle)"
    )

    # skillctl bump
    bump_p = sub.add_parser("bump", help="Bump skill version (in skill.yaml)")
    bump_p.add_argument("path", nargs="?", default=".", help="Path to skill directory")
    bump_p.add_argument("--major", action="store_true", help="Bump major version")
    bump_p.add_argument("--minor", action="store_true", help="Bump minor version")
    bump_p.add_argument("--patch", action="store_true", help="Bump patch version (default)")

    # skillctl auth / rbac / namespace (Milestone 1 — RBAC)
    register_rbac_commands(sub)

    # skillctl policy / observe (Milestone 2 — runtime policy + observability)
    register_policy_commands(sub)

    # skillctl compliance / deploy (Milestone 3 — compliance + progressive deploy)
    register_compliance_commands(sub)
    register_deploy_commands(sub)

    # skillctl ci / forensics / identity (Milestone 4 — enterprise scale)
    register_enterprise_commands(sub)

    # skillctl serve
    serve_p = sub.add_parser("serve", help="Start the skill registry server")
    serve_p.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    serve_p.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    serve_p.add_argument("--data-dir", default=None, help="Data directory (default: ~/.skillctl/registry)")
    serve_p.add_argument(
        "--auth-disabled",
        action="store_true",
        help="Disable authentication (dev only — refuses to bind to non-loopback hosts)",
    )
    serve_p.add_argument(
        "--hmac-key",
        default=None,
        help="HMAC key for audit log signing (or set SKILLCTL_HMAC_KEY env var)",
    )
    serve_p.add_argument(
        "--auto-generate-hmac-key",
        action="store_true",
        help="Auto-generate an HMAC key in data_dir if none is supplied (development only)",
    )
    serve_p.add_argument(
        "--allowed-host",
        action="append",
        dest="allowed_hosts",
        default=[],
        help="Additional Host header value to accept (repeatable)",
    )
    serve_p.add_argument(
        "--cors-origin",
        action="append",
        dest="cors_origins",
        default=[],
        help="CORS allow_origins entry (repeatable; default: none)",
    )
    serve_p.add_argument("--log-level", default="info", help="Log level (default: info)")
    serve_p.add_argument(
        "--storage",
        default="filesystem",
        choices=["filesystem", "github"],
        help="Storage backend (default: filesystem)",
    )
    serve_p.add_argument("--github-repo", default=None, help="GitHub repo URL (for github backend)")
    serve_p.add_argument("--github-token", default=None, help="GitHub PAT (for github backend)")
    serve_p.add_argument("--github-branch", default="main", help="GitHub branch (default: main)")

    # skillctl token (subcommands)
    token_p = sub.add_parser("token", help="Manage registry API tokens")
    token_sub = token_p.add_subparsers(dest="token_command")
    token_create_p = token_sub.add_parser("create", help="Create a new API token")
    token_create_p.add_argument("--name", required=True, help="Token name")
    token_create_p.add_argument(
        "--scope", action="append", dest="scopes", default=[], help="Permission scope (repeatable)"
    )
    token_create_p.add_argument("--expires", default=None, help="Expiry duration (e.g. 90d)")
    token_create_p.add_argument("--registry-url", default=None, help="Registry URL (overrides config)")
    token_create_p.add_argument("--token", default=None, help="Auth token (overrides config)")

    # skillctl config (subcommands)
    config_p = sub.add_parser("config", help="Manage skillctl configuration")
    config_sub = config_p.add_subparsers(dest="config_command")
    config_set_p = config_sub.add_parser("set", help="Set a config value")
    config_set_p.add_argument("key", help="Config key (e.g. registry.url)")
    config_set_p.add_argument("value", help="Config value")
    config_get_p = config_sub.add_parser("get", help="Get a config value")
    config_get_p.add_argument("key", help="Config key (e.g. registry.url)")

    # skillctl login
    login_p = sub.add_parser("login", help="Authenticate with GitHub via device flow")
    login_p.add_argument("--client-id", default=None, help="GitHub OAuth App client ID")
    login_p.add_argument("--scopes", default="repo", help="OAuth scopes (default: repo)")

    # skillctl logout
    sub.add_parser("logout", help="Remove stored GitHub credentials")

    # skillctl configure
    sub.add_parser("configure", help="Interactive setup wizard for registry, optimizer, and auth")

    # -----------------------------------------------------------------------
    # BACKWARD-COMPATIBLE ALIASES
    # -----------------------------------------------------------------------

    # skillctl init <name> → create skill <name>
    init_p = sub.add_parser("init", help="[alias] Create a new skill (same as 'create skill')")
    init_p.add_argument("name", help="Skill name (namespace/skill-name)")

    # skillctl push [path] → apply --local
    push_p = sub.add_parser("push", help="[alias] Push skill to local store (same as 'apply --local')")
    push_p.add_argument("path", nargs="?", default=".", help="Path to skill")
    push_p.add_argument("--dry-run", action="store_true", help="Show what would happen")

    # skillctl pull <ref> → get skill <ref>
    pull_p = sub.add_parser("pull", help="[alias] Pull skill from local store (same as 'get skill')")
    pull_p.add_argument("ref", help="namespace/name@version")
    pull_p.add_argument("--output", "-o", default=".", help="Output directory")

    # skillctl list → get skills
    list_p = sub.add_parser("list", help="[alias] List skills in local store (same as 'get skills')")
    list_p.add_argument("--namespace", help="Filter by namespace")
    list_p.add_argument("--tag", help="Filter by tag")
    list_p.add_argument("--json", action="store_true", help="Output as JSON")

    # skillctl publish [path] → apply (remote)
    publish_p = sub.add_parser("publish", help="[alias] Publish skill to remote registry (same as 'apply')")
    publish_p.add_argument("path", nargs="?", default=".", help="Path to skill directory or manifest")
    publish_p.add_argument("--registry-url", default=None, help="Registry URL (overrides config)")
    publish_p.add_argument("--token", default=None, help="Auth token (overrides config)")

    # skillctl search [query] → get skills --remote
    search_p = sub.add_parser("search", help="[alias] Search remote registry (same as 'get skills --remote')")
    search_p.add_argument("query", nargs="?", default=None, help="Search query")
    search_p.add_argument("--namespace", default=None, help="Filter by namespace")
    search_p.add_argument("--tag", default=None, help="Filter by tag")
    search_p.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    search_p.add_argument("--registry-url", default=None, help="Registry URL (overrides config)")
    search_p.add_argument("--token", default=None, help="Auth token (overrides config)")

    # skillctl export [--output <path>] [--format tar.gz|zip] [--namespace <ns>] [--tag <tag>]
    export_p = sub.add_parser("export", help="Export skills from local store to a portable archive")
    export_p.add_argument(
        "--output", "-o", default=None, help="Output file path (default: skillctl-export-{timestamp}.tar.gz)"
    )
    export_p.add_argument(
        "--format", default="tar.gz", choices=["tar.gz", "zip"], help="Archive format (default: tar.gz)"
    )
    export_p.add_argument("--namespace", default=None, help="Filter by namespace")
    export_p.add_argument("--tag", default=None, help="Filter by tag")

    # skillctl import <archive>
    import_p = sub.add_parser("import", help="Import skills from an archive (reverse of export)")
    import_p.add_argument("archive", help="Path to archive file (tar.gz or zip)")

    # skillctl install <ref-or-path> --target <targets> [--global] [--force]
    install_p = sub.add_parser(
        "install",
        help="Install a skill to AI coding IDEs (auto-applies if given a local path)",
    )
    install_p.add_argument(
        "ref", nargs="?", default=None, help="Skill ref (namespace/name@version) or path to skill directory"
    )
    install_p.add_argument("--from-url", default=None, help="Download SKILL.md from URL and install")
    install_p.add_argument(
        "--target", required=True, help="Target IDEs: claude,cursor,windsurf,copilot,kiro (comma-separated or 'all')"
    )
    install_p.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="Install to user-level directory (claude, windsurf, kiro only)",
    )
    install_p.add_argument("--force", action="store_true", help="Overwrite modified files")
    install_p.add_argument(
        "--dry-run", action="store_true", help="Preview what would be installed without writing files"
    )

    # skillctl uninstall <ref> --target <targets>
    uninstall_p = sub.add_parser("uninstall", help="Remove a skill from AI coding IDEs")
    uninstall_p.add_argument("ref", help="Skill ref (namespace/name@version)")
    uninstall_p.add_argument(
        "--target", required=True, help="Target IDEs: claude,cursor,windsurf,copilot,kiro (comma-separated or 'all')"
    )

    # -----------------------------------------------------------------------
    # DISPATCH
    # -----------------------------------------------------------------------

    args, remaining = parser.parse_known_args()

    try:
        # Primary kubectl-style commands
        if args.command == "apply":
            cmd_apply(args)
        elif args.command == "create":
            cmd_create(args)
        elif args.command == "get":
            cmd_get(args)
        elif args.command == "describe":
            cmd_describe(args)
        elif args.command == "delete":
            cmd_delete(args)
        elif args.command == "logs":
            cmd_logs(args)
        elif args.command == "export":
            cmd_export(args)
        elif args.command == "import":
            cmd_import(args)
        elif args.command == "install":
            cmd_install(args)
        elif args.command == "uninstall":
            cmd_uninstall(args)

        # Existing commands (unchanged)
        elif args.command == "validate":
            cmd_validate(args)
        elif args.command == "version":
            cmd_version()
        elif args.command == "diff":
            cmd_diff(args)
        elif args.command == "doctor":
            cmd_doctor(args)
        elif args.command == "eval":
            cmd_eval_passthrough(remaining)
        elif args.command == "bump":
            cmd_bump(args)
        elif args.command == "serve":
            cmd_serve(args)
        elif args.command == "auth":
            sys.exit(dispatch_auth(args))
        elif args.command == "rbac":
            sys.exit(dispatch_rbac(args))
        elif args.command == "namespace":
            sys.exit(dispatch_namespace(args))
        elif args.command == "policy":
            sys.exit(dispatch_policy(args))
        elif args.command == "observe":
            sys.exit(dispatch_observe(args))
        elif args.command == "compliance":
            sys.exit(dispatch_compliance(args))
        elif args.command == "deploy":
            sys.exit(dispatch_deploy(args))
        elif args.command == "ci":
            sys.exit(dispatch_ci(args))
        elif args.command == "forensics":
            sys.exit(dispatch_forensics(args))
        elif args.command == "identity":
            sys.exit(dispatch_identity(args))
        elif args.command == "token":
            cmd_token(args)
        elif args.command == "config":
            cmd_config(args)
        elif args.command == "login":
            cmd_login(args)
        elif args.command == "logout":
            cmd_logout()
        elif args.command == "configure":
            cmd_configure()

        # Backward-compatible aliases
        elif args.command == "init":
            cmd_create_skill(args)
        elif args.command == "push":
            # push → apply --local
            args.local = True
            args.file = None
            args.registry_url = None
            args.token = None
            cmd_apply(args)
        elif args.command == "pull":
            # pull → get skill
            args.remote = False
            args.registry_url = None
            args.token = None
            cmd_get_skill(args)
        elif args.command == "list":
            # list → get skills (local)
            args.remote = False
            args.query = None
            args.limit = 20
            args.registry_url = None
            args.token = None
            cmd_get_skills(args)
        elif args.command == "publish":
            # publish → apply (remote)
            args.dry_run = False
            args.local = False
            args.file = None
            cmd_apply(args)
        elif args.command == "search":
            # search → get skills --remote
            args.remote = True
            args.json = False
            cmd_get_skills_remote(args)

        else:
            parser.print_help()
            sys.exit(1)
    except SkillctlError as e:
        print(e.format_human(), file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# PRIMARY COMMAND HANDLERS
# ---------------------------------------------------------------------------


def cmd_apply(args):
    """Validate + push to local store. If registry configured, also publish remotely.

    Thin CLI shim over :func:`skillctl._cli_helpers.apply_skill`.  The
    library function does the lifecycle; this handler keeps the CLI's
    validation-error formatting (multi-line inline output, exit 1) and
    the success/breadcrumb output.
    """
    from skillctl._cli_helpers import apply_skill

    path = args.file or args.path

    try:
        result = apply_skill(
            path,
            dry_run=args.dry_run,
            local=getattr(args, "local", False),
            registry_url=getattr(args, "registry_url", None),
            token=getattr(args, "token", None),
        )
    except SkillctlError as e:
        if e.code == "E_VALIDATION":
            # Preserve the pre-refactor inline-per-error format.  The
            # structured errors list is carried as an attribute on the
            # exception so we don't have to re-parse the multi-line
            # ``why`` field (which would mis-render any future
            # validator message containing a literal ``]``).
            print("Validation errors — cannot apply:", file=sys.stderr)
            errors = getattr(e, "errors", None)
            if errors:
                for ve in errors:
                    print(f"  ✗ [{ve.code}] {ve.message}", file=sys.stderr)
            else:
                # Fallback: render the multi-line ``why`` line by line.
                for line in e.why.splitlines():
                    stripped = line.strip()
                    if stripped:
                        print(f"  ✗ {stripped}", file=sys.stderr)
            sys.exit(1)
        raise

    ref = result.ref

    if result.local_status == "dry-run":
        push_result = result.push_result
        print(f"Dry run — would apply {ref}")
        if push_result is not None:
            print(f"  Hash: {push_result.hash}")
            print(f"  Size: {push_result.size} bytes")
            print(f"  New: {push_result.created}")
        return

    # Security-gate stderr printing lives here — it's CLI presentation.
    if result.remote_status == "blocked (security)":
        n = len(result.critical_findings)
        print(f"Security gate: {n} CRITICAL finding(s) — publish blocked:", file=sys.stderr)
        for f in result.critical_findings:
            print(f"  ✗ [{f.code}] {f.title}", file=sys.stderr)
            if f.detail:
                print(f"    {f.detail}", file=sys.stderr)
        print("\nFix the findings above, or use --local to push without publishing.", file=sys.stderr)

    # 6. Print summary (same shape as before).
    registry_url = _get_registry_url(args)
    if result.local_status == "unchanged" and result.remote_status is None:
        print(f"✓ Applied {ref} (unchanged)")
    elif result.remote_status == "published":
        print(f"✓ Applied {ref} (local + remote)")
    elif result.remote_status and result.remote_status.startswith("failed"):
        print(f"✓ Applied {ref} (local only — remote {result.remote_status})")
    else:
        scope = "local only" if getattr(args, "local", False) or not registry_url else "local"
        print(f"✓ Applied {ref} ({scope})")
    if result.local_status != "unchanged" and result.push_result is not None:
        print(f"  Hash: {result.push_result.hash}")

    # Breadcrumb stays in the CLI — only `cmd_apply` users get the
    # nudge; `cmd_install` calls `apply_skill` directly so it never
    # passes through here.
    if not _output_is_machine(args):
        print(f"\n  Next: skillctl install {ref} --target all", file=sys.stderr)


def cmd_create(args):
    """Dispatch 'create' subcommands."""
    if args.create_resource == "skill":
        cmd_create_skill(args)
    else:
        print("Usage: skillctl create skill <name>", file=sys.stderr)
        sys.exit(1)


def cmd_create_skill(args):
    """Scaffold a new skill project."""
    name = args.name
    skill_yaml = (
        f"apiVersion: skillctl.io/v1\n"
        f"kind: Skill\n"
        f"\n"
        f"metadata:\n"
        f"  name: {name}\n"
        f"  version: 0.1.0\n"
        f'  description: ""\n'
        f"\n"
        f"spec:\n"
        f"  content:\n"
        f"    path: ./SKILL.md\n"
        f"  capabilities:\n"
        f"    - read_file\n"
    )
    skill_md = (
        f"# {name.split('/')[-1] if '/' in name else name}\n"
        f"\n"
        f"## Description\n"
        f"\n"
        f"Describe what this skill does.\n"
        f"\n"
        f"## Instructions\n"
        f"\n"
        f"Add skill instructions here.\n"
    )

    for fname in ("skill.yaml", "SKILL.md"):
        if Path(fname).exists():
            raise SkillctlError(
                code="E_FILE_EXISTS",
                what=f"{fname} already exists in the current directory",
                why="Creating a skill would overwrite your existing file",
                fix=f"Remove {fname} first, or run this command in an empty directory",
            )

    short_name = name.split("/")[-1] if "/" in name else name

    Path("skill.yaml").write_text(skill_yaml)
    Path("SKILL.md").write_text(skill_md)

    evals_dir = Path("evals")
    evals_dir.mkdir(exist_ok=True)

    evals_json = evals_dir / "evals.json"
    if not evals_json.exists():
        evals_data = [
            {
                "id": f"{short_name}-eval-1",
                "prompt": f"Use the {short_name} skill on a sample task",
                "assertions": [
                    "output is relevant to the skill's purpose",
                    "does not contain errors",
                ],
            }
        ]
        evals_json.write_text(json.dumps(evals_data, indent=2) + "\n")

    queries_json = evals_dir / "eval_queries.json"
    if not queries_json.exists():
        queries_data = [
            {"query": f"Help me with {short_name}", "should_trigger": True},
            {"query": "What is the weather today?", "should_trigger": False},
        ]
        queries_json.write_text(json.dumps(queries_data, indent=2) + "\n")

    print("✓ Skill scaffolded: skill.yaml + SKILL.md + evals/")
    if not _output_is_machine(args):
        print("\n  Next: edit SKILL.md, then `skillctl validate`", file=sys.stderr)


def cmd_get(args):
    """Dispatch 'get' subcommands."""
    if args.get_resource == "skills":
        if getattr(args, "remote", False):
            cmd_get_skills_remote(args)
        else:
            cmd_get_skills(args)
    elif args.get_resource == "skill":
        cmd_get_skill(args)
    elif args.get_resource == "installations":
        cmd_get_installations(args)
    else:
        print("Usage: skillctl get skills [--remote]", file=sys.stderr)
        print("       skillctl get skill <ref>", file=sys.stderr)
        print("       skillctl get installations [--target <ide>]", file=sys.stderr)
        sys.exit(1)


def cmd_get_skills(args):
    """List skills from local store."""
    store = ContentStore()
    ns: str | None = getattr(args, "namespace", None)
    tg: str | None = getattr(args, "tag", None)
    entries = store.list_skills(
        namespace=ns,
        tag=tg,
    )

    if getattr(args, "json", False):
        print(json.dumps([e.__dict__ for e in entries], indent=2))
    else:
        if not entries:
            print("No skills in local store.")
        else:
            for e in entries:
                tags = f" [{', '.join(e.tags)}]" if e.tags else ""
                print(f"  {e.name}@{e.version}  ({e.size} bytes){tags}")


def cmd_get_skills_remote(args):
    """Search the remote registry for skills."""
    registry_url = _require_registry_url(args)
    token = _get_registry_token(args)

    params = []
    query = getattr(args, "query", None)
    if query:
        params.append(f"q={urllib.parse.quote(query)}")
    namespace = getattr(args, "namespace", None)
    if namespace:
        params.append(f"namespace={urllib.parse.quote(namespace)}")
    tag = getattr(args, "tag", None)
    if tag:
        params.append(f"tag={urllib.parse.quote(tag)}")
    limit = getattr(args, "limit", 20)
    params.append(f"limit={limit}")

    url = f"{registry_url}/api/v1/skills"
    if params:
        url += "?" + "&".join(params)

    raw = _registry_request("GET", url, token=token)
    data = json.loads(raw.decode())

    skills = data.get("skills", [])
    total = data.get("total", len(skills))

    if not skills:
        print("No skills found.")
        return

    name_w, ver_w, grade_w = 30, 10, 6
    desc_w = 40
    header = f"{'NAME':<{name_w}} {'VERSION':<{ver_w}} {'GRADE':<{grade_w}} {'DESCRIPTION':<{desc_w}}"
    print(header)
    print("-" * len(header))
    for s in skills:
        name = s.get("name", "")[:name_w]
        version = s.get("version", "")[:ver_w]
        grade = s.get("eval_grade") or "-"
        desc = (s.get("description", "") or "")[:desc_w]
        print(f"{name:<{name_w}} {version:<{ver_w}} {grade:<{grade_w}} {desc:<{desc_w}}")

    print(f"\nShowing {len(skills)} of {total} results")


def cmd_get_skill(args):
    """Pull/show a specific skill by name@version."""
    name, version = _parse_ref(args.ref)

    if getattr(args, "remote", False):
        # Pull the complete artifact from the remote registry.
        from skillctl.artifact import extract_artifact

        registry_url = _require_registry_url(args)
        token = _get_registry_token(args)
        ns, skill_name = name.split("/", 1) if "/" in name else ("", name)
        output_dir = Path(getattr(args, "output", "."))
        artifact_url = f"{registry_url}/api/v1/skills/{ns}/{skill_name}/{version}/artifact"
        try:
            artifact = _registry_request("GET", artifact_url, token=token)
            extract_artifact(
                artifact,
                output_dir,
                expected_name=name,
                expected_version=version,
            )
            print(f"✓ Pulled {name}@{version} from remote to {output_dir}")
        except SkillctlError as exc:
            # Compatibility with registries released before complete artifacts.
            if exc.code != "E_REGISTRY_HTTP" or "HTTP 404" not in exc.what:
                raise
            content_url = f"{registry_url}/api/v1/skills/{ns}/{skill_name}/{version}/content"
            content = _registry_request("GET", content_url, token=token)
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / "SKILL.md"
            output_file.write_bytes(content)
            print(f"✓ Pulled {name}@{version} from legacy remote to {output_file}")
    else:
        # Pull and verify the complete artifact from the local store.
        from skillctl.artifact import extract_artifact

        store = ContentStore()
        artifact, entry = store.pull_artifact(name, version)
        output_dir = Path(getattr(args, "output", "."))
        extract_artifact(
            artifact,
            output_dir,
            expected_name=name,
            expected_version=version,
        )
        print(f"✓ Pulled {name}@{version} to {output_dir}")
        print(f"  Size: {entry['size']} bytes")
        print(f"  Hash: {entry['hash']}")


def cmd_describe(args):
    """Dispatch 'describe' subcommands."""
    if args.describe_resource == "skill":
        cmd_describe_skill(args)
    else:
        print("Usage: skillctl describe skill <ref>", file=sys.stderr)
        sys.exit(1)


def cmd_describe_skill(args):
    """Show rich detail for a skill version."""
    name, version = _parse_ref(args.ref)
    store = ContentStore()

    # Pull to verify it exists
    content, entry = store.pull(name, version)

    # Load manifest
    prefix = entry["hash"][:2]
    manifest_path = store.store_dir / prefix / f"{entry['hash']}.manifest.yaml"
    manifest_data = {}
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest_data = yaml.safe_load(f) or {}

    meta = manifest_data.get("metadata", {})
    spec = manifest_data.get("spec", {})

    # Get all versions
    all_versions = store.list_versions(name)

    if getattr(args, "json", False):
        output = {
            "name": name,
            "version": version,
            "description": meta.get("description", ""),
            "tags": entry.get("tags", []),
            "license": meta.get("license", ""),
            "hash": entry["hash"],
            "pushed_at": entry.get("pushed_at", ""),
            "size": entry["size"],
            "parameters": spec.get("parameters", []),
            "capabilities": spec.get("capabilities", []),
            "versions": [v.version for v in all_versions],
        }
        print(json.dumps(output, indent=2))
    else:
        tags_str = ", ".join(entry.get("tags", [])) or "(none)"
        license_str = meta.get("license", "(not set)")
        desc_str = meta.get("description", "(no description)")

        print(f"Name:        {name}")
        print(f"Version:     {version}")
        print(f"Description: {desc_str}")
        print(f"Tags:        {tags_str}")
        print(f"License:     {license_str}")
        print(f"Hash:        {entry['hash']}")
        print(f"Pushed:      {entry.get('pushed_at', '(unknown)')}")
        print(f"Size:        {entry['size']} bytes")

        params = spec.get("parameters", [])
        if params:
            print("\nParameters:")
            for p in params:
                p_type = p.get("type", "string")
                p_default = p.get("default", "")
                p_values = p.get("values", [])
                detail = f"  {p.get('name', '?'):<16} {p_type}"
                if p_values:
                    detail += f"  [{', '.join(p_values)}]"
                if p_default:
                    detail += f"  default: {p_default}"
                print(detail)

        caps = spec.get("capabilities", [])
        if caps:
            print("\nCapabilities:")
            print(f"  {', '.join(caps)}")

        if all_versions:
            print("\nVersions in store:")
            for v in all_versions:
                marker = "  (current)" if v.version == version else ""
                print(f"  {v.version}{marker}")


def cmd_delete(args):
    """Dispatch 'delete' subcommands."""
    if args.delete_resource == "skill":
        cmd_delete_skill(args)
    else:
        print("Usage: skillctl delete skill <ref> [--force]", file=sys.stderr)
        sys.exit(1)


def cmd_delete_skill(args):
    """Remove a skill version from local store."""
    name, version = _parse_ref(args.ref)
    ref = f"{name}@{version}"

    if not args.force:
        try:
            answer = input(f"Delete {ref}? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)
        if answer.strip().lower() != "y":
            print("Aborted.")
            sys.exit(0)

    store = ContentStore()
    store.delete_skill(name, version)
    print(f"✓ Deleted {ref}")


def cmd_export(args):
    """Export skills from the local store to a portable archive."""
    from datetime import datetime, timezone

    store = ContentStore()
    fmt = getattr(args, "format", "tar.gz")
    output = getattr(args, "output", None)

    if not output:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ext = "tar.gz" if fmt == "tar.gz" else "zip"
        output = f"skillctl-export-{timestamp}.{ext}"

    output_path = Path(output)
    namespace = getattr(args, "namespace", None)
    tag = getattr(args, "tag", None)

    result = store.export_skills(
        output_path=output_path,
        format=fmt,
        namespace=namespace,
        tag=tag,
    )

    print(f"✓ Exported {result['skill_count']} skill(s) to {result['path']}")
    print(f"  Format: {result['format']}")
    print(f"  Size: {result['total_size']} bytes")


def cmd_import(args):
    """Import skills from an archive."""
    archive_path = Path(args.archive)
    if not archive_path.exists():
        raise SkillctlError(
            code="E_NOT_FOUND",
            what=f"Archive not found: {archive_path}",
            why="The archive file must exist",
            fix="Check the path and try again",
        )
    store = ContentStore()
    result = store.import_skills(archive_path)
    print(f"✓ Imported {result['imported_count']} skill(s)")
    if result["skipped_count"]:
        print(f"  Skipped {result['skipped_count']} (already in store)")
    if result["errors"]:
        for err in result["errors"]:
            print(f"  ✗ {err}", file=sys.stderr)


def cmd_bump(args):
    """Bump skill version in skill.yaml."""
    path = Path(args.path)
    yaml_path = path / "skill.yaml" if path.is_dir() else path

    if not yaml_path.exists():
        raise SkillctlError(
            code="E_NO_MANIFEST",
            what=f"No skill.yaml found at {yaml_path}",
            why="Version bump requires a skill.yaml file",
            fix="Run this command in a skill directory with skill.yaml",
        )

    content = yaml_path.read_text()
    import re

    match = re.search(r'version:\s*["\']?(\d+)\.(\d+)\.(\d+)["\']?', content)
    if not match:
        raise SkillctlError(
            code="E_NO_VERSION",
            what="Could not find version field in skill.yaml",
            why="Version must be in semver format (MAJOR.MINOR.PATCH)",
            fix="Add 'version: 1.0.0' to metadata section",
        )

    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))

    if args.major:
        major += 1
        minor = 0
        patch = 0
    elif args.minor:
        minor += 1
        patch = 0
    else:
        patch += 1

    old_version = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
    new_version = f"{major}.{minor}.{patch}"
    new_content = content.replace(match.group(0), match.group(0).replace(old_version, new_version))
    yaml_path.write_text(new_content)
    print(f"✓ Version bumped: {old_version} → {new_version}")


def cmd_logs(args):
    """Show audit trail for a skill from the registry."""
    registry_url = _require_registry_url(args)
    token = _get_registry_token(args)

    url = f"{registry_url}/api/v1/audit?limit=100"

    raw = _registry_request("GET", url, token=token, timeout=10)
    try:
        response = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Error parsing audit log response: {e}", file=sys.stderr)
        sys.exit(1)

    # /api/v1/audit returns {"events": [...], "integrity": {...}}.
    # Older response shapes were a flat list; accept both for compatibility.
    if isinstance(response, dict):
        events = response.get("events", [])
    else:
        events = response

    if not events:
        print(f"No audit events found for '{args.name}'.")
        return

    # Filter by skill name
    skill_events = [e for e in events if args.name in e.get("resource", "")]
    if not skill_events:
        print(f"No audit events found for '{args.name}'.")
        return

    for event in skill_events:
        ts = event.get("timestamp", "")[:19]
        action = event.get("action", "")
        actor = event.get("actor", "")
        resource = event.get("resource", "")
        print(f"  {ts}  {action:20s}  {actor:15s}  {resource}")


def _print_inner_apply_summary(result) -> None:
    """Print the one-line ``✓ Applied`` confirmation for the apply step
    that runs transparently inside ``cmd_install``.

    Mirrors the user-visible output `cmd_apply` produced when
    ``cmd_install`` used to call it via a synthetic ``argparse.Namespace``
    — kept here so refactoring didn't quietly drop the line.
    """
    ref = result.ref
    if result.local_status == "unchanged" and result.remote_status is None:
        print(f"✓ Applied {ref} (unchanged)")
    elif result.remote_status == "published":
        print(f"✓ Applied {ref} (local + remote)")
    elif result.remote_status and result.remote_status.startswith("failed"):
        print(f"✓ Applied {ref} (local only — remote {result.remote_status})")
    else:
        print(f"✓ Applied {ref} (local only)")
    if result.local_status not in ("unchanged", "dry-run") and result.push_result is not None:
        print(f"  Hash: {result.push_result.hash}")


def cmd_install(args):
    """Install a skill to AI coding IDEs."""
    import tempfile

    from skillctl._cli_helpers import apply_skill
    from skillctl.install import download_skill, install_skill

    ref = args.ref
    from_url = getattr(args, "from_url", None)
    targets = [t.strip() for t in args.target.split(",")]

    if from_url:
        # Download from URL, apply locally, then install.  apply_skill
        # returns the canonical ref so we don't need to reload the
        # manifest just to compute name@version.
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_dir = download_skill(from_url, Path(tmp_dir))
            print(f"Downloaded skill to {skill_dir}")
            apply_result = apply_skill(str(skill_dir), local=True)
            _print_inner_apply_summary(apply_result)
            ref = apply_result.ref
    elif ref is None:
        raise SkillctlError(
            code="E_MISSING_REF",
            what="No skill ref or --from-url provided",
            why="The install command needs a skill to install",
            fix="Provide a skill ref (namespace/name@version), a path, or --from-url <url>",
        )
    elif "@" not in ref and Path(ref).expanduser().exists():
        # If ref looks like a local path (no @version), apply first.
        path = str(Path(ref).expanduser())
        print(f"Applying {path} first...")
        apply_result = apply_skill(path, local=True)
        _print_inner_apply_summary(apply_result)
        ref = apply_result.ref

    results = install_skill(
        ref=ref,
        targets=targets,
        global_scope=args.global_scope,
        force=args.force,
        dry_run=args.dry_run,
    )
    for r in results:
        status = "✓" if r.success else "✗"
        print(f"  {status} {r.target}: {r.message}")


def cmd_uninstall(args):
    """Uninstall a skill from AI coding IDEs."""
    from skillctl.install import uninstall_skill

    targets = [t.strip() for t in args.target.split(",")]
    results = uninstall_skill(ref=args.ref, targets=targets)
    for r in results:
        status = "✓" if r.success else "✗"
        print(f"  {status} {r.target}: {r.message}")


def cmd_get_installations(args):
    """List skills installed to IDEs."""
    from skillctl.install import list_installations

    target = getattr(args, "target", None)
    data = list_installations(target=target)

    if getattr(args, "json", False):
        serializable = {}
        for ref, targets in data.items():
            if isinstance(targets, dict):
                serializable[ref] = {t: r.to_dict() if hasattr(r, "to_dict") else r for t, r in targets.items()}
            else:
                serializable[ref] = targets.to_dict() if hasattr(targets, "to_dict") else str(targets)
        print(json.dumps(serializable, indent=2))
        return

    if not data:
        print("No installations found.")
        return

    for ref, targets in data.items():
        print(f"\n{ref}:")
        if isinstance(targets, dict):
            for t, record in targets.items():
                path = record.path if hasattr(record, "path") else record.get("path", "")
                scope = record.scope if hasattr(record, "scope") else record.get("scope", "")
                print(f"  {t}: {path} ({scope})")
        else:
            path = targets.path if hasattr(targets, "path") else ""
            print(f"  {path}")


# ---------------------------------------------------------------------------
# EXISTING COMMAND HANDLERS (unchanged logic)
# ---------------------------------------------------------------------------


def cmd_validate(args):
    """Validate a skill manifest."""
    # Resolve effective format: explicit --format wins over the legacy
    # --json shorthand, which wins over the default text.
    fmt = getattr(args, "format", None)
    if fmt is None:
        fmt = "json" if getattr(args, "json", False) else "text"

    # `--format=github` mode runs in CI, where a malformed YAML PR diff
    # is the most useful place for a PR annotation.  Wrap the manifest
    # load so the user sees one ::error:: instead of a Python
    # traceback.
    target_path = Path(args.path)
    skill_yaml = target_path / "skill.yaml" if target_path.is_dir() else target_path
    skill_md = target_path / "SKILL.md" if target_path.is_dir() else None

    loader = ManifestLoader()
    validator = SchemaValidator()

    if fmt == "github":
        try:
            manifest, load_warnings = loader.load(args.path)
        except Exception as e:
            from skillctl.eval.report import format_github_validation

            skill_name = target_path.name or "skill"
            issues = [
                {
                    "severity": "error",
                    "code": "VAL-LOAD",
                    "message": "Failed to load manifest",
                    "path": "",
                    "hint": str(e),
                    "file": str(skill_yaml) if skill_yaml.exists() else None,
                }
            ]
            format_github_validation(skill_name=skill_name, issues=issues)
            sys.exit(1)
    else:
        manifest, load_warnings = loader.load(args.path)

    result = validator.validate(manifest)

    # Resolve content for capability check
    base_dir = str(Path(args.path).parent) if Path(args.path).is_file() else args.path
    try:
        content = loader.resolve_content(manifest, base_dir)
    except Exception as e:
        if fmt != "github":
            print(f"Warning: Could not resolve skill content for capability check: {e}", file=sys.stderr)
        content = ""

    cap_warnings = validator.check_capabilities(manifest, content)

    # Merge warnings from all sources (preserved for json/text output).
    all_warnings = []
    for w in load_warnings:
        all_warnings.append({"code": w.code, "message": w.message, "hint": w.hint})
    for w in result.warnings:
        all_warnings.append({"code": w.code, "message": w.message, "path": w.path, "hint": w.hint})
    for w in cap_warnings:
        all_warnings.append({"code": w.code, "message": w.message, "hint": w.hint})

    if fmt == "json":
        output = {
            "valid": result.valid,
            "errors": [{"code": e.code, "message": e.message, "path": e.path, "hint": e.hint} for e in result.errors],
            "warnings": all_warnings,
            "strict": getattr(args, "strict", False),
        }
        print(json.dumps(output, indent=2))
    elif fmt == "github":
        from skillctl.eval.report import format_github_validation

        # Per-issue file routing: load_warnings come from frontmatter
        # parsing, so they bind to SKILL.md.  Schema errors / warnings
        # / capability warnings come from the manifest as a whole, so
        # they bind to skill.yaml when present, otherwise SKILL.md.
        manifest_file = (
            str(skill_yaml) if skill_yaml.exists() else (str(skill_md) if skill_md and skill_md.exists() else None)
        )
        skill_md_file = str(skill_md) if skill_md and skill_md.exists() else manifest_file

        issues: list[dict] = []
        for e in result.errors:
            issues.append(
                {
                    "severity": "error",
                    "code": e.code,
                    "message": e.message,
                    "path": e.path,
                    "hint": e.hint,
                    "file": manifest_file,
                }
            )
        for w in load_warnings:
            issues.append(
                {
                    "severity": "warning",
                    "code": w.code,
                    "message": w.message,
                    "path": "",
                    "hint": w.hint,
                    "file": skill_md_file,
                }
            )
        for w in result.warnings:
            issues.append(
                {
                    "severity": "warning",
                    "code": w.code,
                    "message": w.message,
                    "path": w.path,
                    "hint": w.hint,
                    "file": manifest_file,
                }
            )
        for w in cap_warnings:
            issues.append(
                {
                    "severity": "warning",
                    "code": w.code,
                    "message": w.message,
                    "path": "",
                    "hint": w.hint,
                    "file": manifest_file,
                }
            )

        skill_name = manifest.metadata.name or target_path.name or "skill"
        format_github_validation(skill_name=skill_name, issues=issues)
    else:
        # Default text format.
        if result.errors:
            print("Validation errors:")
            for e in result.errors:
                print(f"  ✗ [{e.code}] {e.message}")
                print(f"    Path: {e.path}")
                print(f"    Fix: {e.hint}")
        for w in load_warnings:
            print(f"  ⚠ [{w.code}] {w.message}")
            print(f"    Hint: {w.hint}")
        for w in cap_warnings:
            print(f"  ⚠ [{w.code}] {w.message}")
            print(f"    Hint: {w.hint}")
        if result.valid and not all_warnings:
            print("✓ Valid")
        elif result.valid and all_warnings:
            print(f"✓ Valid (with {len(all_warnings)} warning{'s' if len(all_warnings) != 1 else ''})")

        # Breadcrumb on success.  ``_output_is_machine`` already returns
        # True for ``--format != "text"``, so this only fires under the
        # human-readable text format on a TTY.
        if result.valid and not _output_is_machine(args):
            print(f"\n  Next: skillctl eval audit {args.path}", file=sys.stderr)

    # Determine exit code (independent of format).
    if result.errors:
        sys.exit(1)
    elif all_warnings and getattr(args, "strict", False):
        sys.exit(1)
    else:
        sys.exit(0)


def cmd_version():
    """Print version info."""
    print(version_info())


def cmd_diff(args):
    """Compare two skill versions from the local store."""
    store = ContentStore()
    result = diff_skills(store, args.ref_a, args.ref_b)

    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_diff(result))


def cmd_doctor(args):
    """Check the health of the skillctl environment."""
    import shutil
    import subprocess

    warnings_count = 0
    errors_count = 0

    print("skillctl doctor\n")

    # 1. Python version >= 3.10
    ver = sys.version_info
    ver_str = f"{ver.major}.{ver.minor}.{ver.micro}"
    if ver >= (3, 10):
        print(f"  ✓ Python {ver_str} (>= 3.10 required)")
    else:
        print(f"  ✗ Python {ver_str} (>= 3.10 required)")
        errors_count += 1

    # 2. Local store exists and is readable
    store_path = Path.home() / ".skillctl" / "store"
    if store_path.is_dir():
        try:
            skill_count = sum(1 for _ in store_path.rglob("*.manifest.yaml"))
            print(f"  ✓ Local store: {store_path} ({skill_count} skills)")
        except PermissionError:
            print(f"  ✗ Local store: {store_path} (not readable)")
            errors_count += 1
    else:
        print(f"  ⚠ Local store: {store_path} (not found — no skills pushed yet)")
        warnings_count += 1

    # 3. Store index is valid JSON
    index_path = Path.home() / ".skillctl" / "index.json"
    if index_path.exists():
        try:
            json.loads(index_path.read_text())
            print("  ✓ Store index: valid")
        except (json.JSONDecodeError, OSError):
            print("  ✗ Store index: invalid JSON")
            errors_count += 1
    else:
        print("  ⚠ Store index: not found (no skills pushed yet)")
        warnings_count += 1

    # 4. Config file exists and is valid
    typed_cfg = _load_skillctl_config()
    if CONFIG_PATH.exists():
        try:
            print(f"  ✓ Config file: {CONFIG_PATH}")
            print(f"    Registry backend: {typed_cfg.registry.backend}")
            print(f"    Optimizer model: {typed_cfg.optimize.model}")
        except Exception:
            print("  ✗ Config file: invalid")
            errors_count += 1
    else:
        print("  ⚠ Config file: not found (run 'skillctl configure')")
        warnings_count += 1

    # 5. Registry
    if typed_cfg.registry.backend == "agent-registry":
        rid = typed_cfg.registry.agent_registry.registry_id
        if rid:
            print(f"  ✓ Registry: agent-registry ({rid})")
        else:
            print("  ⚠ Registry: agent-registry (no registry_id configured)")
            warnings_count += 1
    else:
        registry_url = typed_cfg.registry.local.url or os.environ.get("SKILLCTL_REGISTRY_URL")
        if registry_url:
            try:
                req = urllib.request.Request(f"{registry_url.rstrip('/')}/api/v1/health", method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    print(f"  ✓ Registry: local ({registry_url}, healthy)")
            except Exception:
                print(f"  ⚠ Registry: local ({registry_url}, unreachable)")
                warnings_count += 1
        else:
            print("  ⚠ Registry: not configured (run 'skillctl configure')")
            warnings_count += 1

    # 6. GitHub token
    gh_token = typed_cfg.github.token
    if gh_token:
        try:
            req = urllib.request.Request("https://api.github.com/user", method="GET")
            req.add_header("Authorization", f"Bearer {gh_token}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                user = json.loads(resp.read().decode())
                print(f"  ✓ GitHub token: valid ({user.get('login', 'unknown')})")
        except Exception:
            print("  ⚠ GitHub token: configured but invalid")
            warnings_count += 1
    else:
        print("  ⚠ GitHub token: not configured")
        warnings_count += 1

    # 7. Git installed
    git_path = shutil.which("git")
    if git_path:
        try:
            git_ver = subprocess.check_output(["git", "--version"], stderr=subprocess.DEVNULL, text=True).strip()
            git_ver_num = git_ver.replace("git version ", "")
            print(f"  ✓ Git: installed ({git_ver_num})")
        except Exception:
            print("  ⚠ Git: found but version check failed")
            warnings_count += 1
    else:
        print("  ⚠ Git: not installed")
        warnings_count += 1

    # 8. Dependencies
    try:
        __import__("yaml")
        print("  ✓ Core deps: pyyaml installed")
    except ImportError:
        print("  ✗ Core deps: pyyaml missing")
        errors_count += 1

    optional_available = []
    optional_missing = []
    for pkg, group in [("fastapi", "server"), ("uvicorn", "server"), ("mcp", "plugin")]:
        try:
            __import__(pkg)
            optional_available.append(f"{pkg} [{group}]")
        except ImportError:
            optional_missing.append(f"{pkg} [{group}]")
    if optional_available:
        print(f"  ✓ Optional deps: {', '.join(optional_available)}")
    if optional_missing:
        print(f"  ⚠ Optional deps not installed: {', '.join(optional_missing)}")
        warnings_count += 1

    # 9. ~/.skillctl/ directory permissions
    skillctl_dir = Path.home() / ".skillctl"
    if skillctl_dir.is_dir():
        dir_mode = skillctl_dir.stat().st_mode & 0o777
        if dir_mode == 0o700:
            print(f"  ✓ ~/.skillctl/ permissions: {oct(dir_mode)} (correct)")
        else:
            print(f"  ⚠ ~/.skillctl/ permissions: {oct(dir_mode)} (expected 0o700)")
            warnings_count += 1
    else:
        print("  ⚠ ~/.skillctl/ directory does not exist yet")
        warnings_count += 1

    # 10. Install target directories writable
    from skillctl.install import detect_targets

    detected = detect_targets()
    if detected:
        print(f"  ✓ Detected IDE targets: {', '.join(detected)}")
    else:
        print("  ⚠ No IDE target directories detected in current directory")
        warnings_count += 1

    # 11. Store consistency check
    store = ContentStore()
    try:
        consistency = store.verify_consistency()
        if consistency["ok"]:
            print("  ✓ Store consistency: OK")
        else:
            if consistency["dangling_refs"]:
                print(f"  ✗ Store consistency: {len(consistency['dangling_refs'])} dangling index ref(s)")
                for ref in consistency["dangling_refs"]:
                    print(f"    - {ref}")
                errors_count += 1
            if consistency["orphaned_blobs"]:
                print(f"  ⚠ Store consistency: {len(consistency['orphaned_blobs'])} orphaned blob(s)")
                for blob in consistency["orphaned_blobs"]:
                    print(f"    - {blob}")
                warnings_count += 1
    except Exception as e:
        print(f"  ⚠ Store consistency: check failed ({e})")
        warnings_count += 1

    # Summary
    print(f"\n{warnings_count} warnings, {errors_count} errors")
    sys.exit(1 if errors_count > 0 else 0)


def cmd_eval_passthrough(remaining_args: list[str]):
    """Delegate eval commands to skillctl.eval.cli."""
    from skillctl.eval.cli import build_parser, _dispatch
    from skillctl.eval.errors import EvalError

    parser = build_parser()
    if not remaining_args:
        parser.print_help()
        sys.exit(0)
    args = parser.parse_args(remaining_args)
    if not getattr(args, "command", None):
        parser.print_help()
        sys.exit(1)
    try:
        exit_code = _dispatch(args)
        sys.exit(exit_code or 0)
    except EvalError as e:
        print(e.format_human(), file=sys.stderr)
        sys.exit(1)


def cmd_serve(args):
    """Start the skill registry server."""
    import uvicorn  # type: ignore[import-not-found]
    from skillctl.registry.config import RegistryConfig
    from skillctl.registry.server import create_app

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else None
    config = RegistryConfig(
        host=args.host,
        port=args.port,
        storage_backend=args.storage,
        github_repo=args.github_repo
        or os.environ.get("SKILLCTL_GITHUB_REPO")
        or _load_config().get("github", {}).get("repo"),
        github_token=args.github_token or os.environ.get("SKILLCTL_GITHUB_TOKEN") or _load_github_token(),
        github_branch=args.github_branch,
        auth_disabled=args.auth_disabled,
        hmac_key=args.hmac_key,
        auto_generate_hmac_key=getattr(args, "auto_generate_hmac_key", False),
        log_level=args.log_level,
        allowed_hosts=tuple(getattr(args, "allowed_hosts", []) or ()),
        cors_allow_origins=tuple(getattr(args, "cors_origins", []) or ()),
    )
    if data_dir is not None:
        config.data_dir = data_dir

    try:
        app = create_app(config)
    except RuntimeError as e:
        raise SkillctlError(
            code="E_SERVER_CONFIG",
            what="Refusing to start the registry server",
            why=str(e),
            fix="Adjust the flags as suggested above and retry.",
        ) from e

    uvicorn.run(app, host=config.host, port=config.port, log_level=config.log_level)


def cmd_token(args):
    """Manage registry API tokens."""
    if args.token_command == "create":
        cmd_token_create(args)
    else:
        print("Usage: skillctl token create --name <name> --scope <scope>", file=sys.stderr)
        sys.exit(1)


def cmd_token_create(args):
    """Create a new API token on the remote registry."""
    registry_url = _require_registry_url(args)
    token = _get_registry_token(args)

    payload = {
        "name": args.name,
        "permissions": args.scopes if args.scopes else ["read"],
    }
    if args.expires:
        expires_str = args.expires.strip()
        if expires_str.endswith("d"):
            try:
                days = int(expires_str[:-1])
                payload["expires_in_days"] = days
            except ValueError:
                print(f"Error: Invalid expiry format '{args.expires}'. Use e.g. '90d'.", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Error: Invalid expiry format '{args.expires}'. Use e.g. '90d'.", file=sys.stderr)
            sys.exit(1)

    url = f"{registry_url}/api/v1/tokens"
    body = json.dumps(payload).encode()
    raw = _registry_request("POST", url, token=token, body=body, content_type="application/json")
    data = json.loads(raw.decode())
    print(data.get("token", ""))


# ---------------------------------------------------------------------------
# Config commands
# ---------------------------------------------------------------------------

_CONFIG_KEY_MAP = {
    # New typed config keys
    "registry.backend": lambda c: c.registry.backend,
    "registry.local.url": lambda c: c.registry.local.url,
    "registry.local.token": lambda c: c.registry.local.token,
    "registry.agent_registry.registry_id": lambda c: c.registry.agent_registry.registry_id,
    "registry.agent_registry.region": lambda c: c.registry.agent_registry.region,
    "optimize.model": lambda c: c.optimize.model,
    "optimize.budget_usd": lambda c: c.optimize.budget_usd,
    "optimize.max_tokens": lambda c: c.optimize.max_tokens,
    "github.token": lambda c: c.github.token,
    "github.client_id": lambda c: c.github.client_id,
    # Backward-compat aliases
    "registry.url": lambda c: c.registry.local.url,
    "registry.token": lambda c: c.registry.local.token,
}

_CONFIG_SETTER_MAP = {
    "registry.backend": lambda c, v: setattr(c.registry, "backend", v),
    "registry.local.url": lambda c, v: setattr(c.registry.local, "url", v),
    "registry.local.token": lambda c, v: setattr(c.registry.local, "token", v),
    "registry.agent_registry.registry_id": lambda c, v: setattr(c.registry.agent_registry, "registry_id", v),
    "registry.agent_registry.region": lambda c, v: setattr(c.registry.agent_registry, "region", v),
    "optimize.model": lambda c, v: setattr(c.optimize, "model", v),
    "optimize.budget_usd": lambda c, v: setattr(c.optimize, "budget_usd", float(v)),
    "optimize.max_tokens": lambda c, v: setattr(c.optimize, "max_tokens", int(v)),
    "github.token": lambda c, v: setattr(c.github, "token", v),
    "github.client_id": lambda c, v: setattr(c.github, "client_id", v),
    # Backward-compat aliases
    "registry.url": lambda c, v: setattr(c.registry.local, "url", v),
    "registry.token": lambda c, v: setattr(c.registry.local, "token", v),
}


def cmd_configure():
    """Interactive setup wizard."""
    config = run_configure_wizard()
    _save_skillctl_config(config)
    print(f"\nConfiguration saved to {CONFIG_PATH}")


def cmd_config(args):
    """Manage skillctl configuration."""
    if args.config_command == "set":
        cmd_config_set(args)
    elif args.config_command == "get":
        cmd_config_get(args)
    else:
        print("Usage: skillctl config set <key> <value>", file=sys.stderr)
        print("       skillctl config get <key>", file=sys.stderr)
        sys.exit(1)


def cmd_config_set(args):
    """Set a config value via typed config."""
    key = args.key
    value = args.value

    if "token" in key.lower() or "secret" in key.lower():
        print(
            "Warning: Sensitive value may appear in shell history. Consider using 'skillctl login' for GitHub auth.",
            file=sys.stderr,
        )

    setter = _CONFIG_SETTER_MAP.get(key)
    if not setter:
        print(f"Error: Unknown config key '{key}'.", file=sys.stderr)
        print(
            f"  Supported keys: {', '.join(sorted(k for k in _CONFIG_KEY_MAP if '.' in k and not k.startswith('registry.url') and not k.startswith('registry.token')))}",
            file=sys.stderr,
        )
        sys.exit(1)

    config = _load_skillctl_config()
    try:
        setter(config, value)
    except (ValueError, TypeError) as e:
        print(f"Error: Invalid value for '{key}': {e}", file=sys.stderr)
        sys.exit(1)

    _save_skillctl_config(config)
    print(f"✓ Set {key} = {value}")


def cmd_config_get(args):
    """Get a config value from typed config."""
    key = args.key

    getter = _CONFIG_KEY_MAP.get(key)
    if not getter:
        print(f"Error: Unknown config key '{key}'.", file=sys.stderr)
        print(
            f"  Supported keys: {', '.join(sorted(k for k in _CONFIG_KEY_MAP if '.' in k and not k.startswith('registry.url') and not k.startswith('registry.token')))}",
            file=sys.stderr,
        )
        sys.exit(1)

    config = _load_skillctl_config()
    value = getter(config)
    if value is None:
        print(f"{key}: (not set)")
    else:
        print(f"{key}: {value}")


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------


def cmd_login(args):
    """Authenticate with GitHub using the device flow."""
    from skillctl.github_auth import (
        get_client_id,
        device_flow_login,
        verify_token,
        save_github_token,
    )

    client_id = get_client_id(args.client_id)
    if not client_id:
        print("Error: No GitHub OAuth App client_id configured.", file=sys.stderr)
        print("  Fix: Run 'skillctl config set github.client_id <your-app-client-id>'", file=sys.stderr)
        print("       or set SKILLCTL_GITHUB_CLIENT_ID env var", file=sys.stderr)
        print("       or pass --client-id <id>", file=sys.stderr)
        print()
        print("  To create an OAuth App: https://github.com/settings/applications/new", file=sys.stderr)
        print("  Enable 'Device Flow' in the app settings.", file=sys.stderr)
        sys.exit(1)

    token = device_flow_login(client_id, scopes=args.scopes)

    user = verify_token(token)
    save_github_token(token)

    print(f"\n✓ Authenticated as {user.get('login', 'unknown')} ({user.get('name', '')})")
    print("  Token saved to ~/.skillctl/config.yaml")


def cmd_logout():
    """Remove stored GitHub credentials.

    Goes through the typed config (``load_config`` / ``save_config``) so
    the file is written atomically with mode 0o600 — no raw-YAML
    round-trip.
    """
    config = _load_skillctl_config()
    if not config.github.token:
        print("Not logged in.")
        return
    config.github.token = None
    _save_skillctl_config(config)
    print("✓ GitHub credentials removed.")


# ---------------------------------------------------------------------------
# Remote publish helper (used by cmd_apply)
# ---------------------------------------------------------------------------


def _publish_to_registry(
    args,
    manifest,
    content: str,
    registry_url: str,
    *,
    artifact: bytes | None = None,
):
    """Create and publish a skill version in the remote registry."""
    import secrets as _secrets

    from skillctl.artifact import build_minimal_artifact

    token = _get_registry_token(args)
    manifest_dict = manifest.to_dict()
    content_bytes = content.encode()
    artifact_bytes = artifact or build_minimal_artifact(manifest, content_bytes)

    boundary = f"----skillctl-{_secrets.token_hex(16)}"
    manifest_json = json.dumps(manifest_dict)

    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="manifest"\r\n'
        f"Content-Type: application/json\r\n"
        f"\r\n"
        f"{manifest_json}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="content"; filename="SKILL.md"\r\n'
        f"Content-Type: application/octet-stream\r\n"
        f"\r\n"
    )
    artifact_part = (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="artifact"; filename="artifact.zip"\r\n'
        f"Content-Type: application/vnd.skillctl.artifact.v1+zip\r\n"
        f"\r\n"
    )
    suffix = f"\r\n--{boundary}--\r\n"
    body = prefix.encode() + content_bytes + artifact_part.encode() + artifact_bytes + suffix.encode()

    url = f"{registry_url}/api/v1/skills"
    _registry_request(
        "POST",
        url,
        token=token,
        body=body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )

    publish_body = json.dumps(
        {
            "name": manifest.metadata.name,
            "version": manifest.metadata.version,
        }
    ).encode()
    _registry_request(
        "POST",
        f"{registry_url}/api/v1/skills/publish",
        token=token,
        body=publish_body,
        content_type="application/json",
    )
