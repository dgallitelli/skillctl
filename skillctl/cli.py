"""skillctl CLI — governance commands for agent skills."""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

import yaml

from skillctl.diff import diff_skills, format_diff
from skillctl.errors import SkillctlError
from skillctl.manifest import ManifestLoader
from skillctl.optimize.cli import register_optimize_commands, handle_optimize
from skillctl.store import ContentStore
from skillctl.validator import SchemaValidator
from skillctl.version import version_info


# ---------------------------------------------------------------------------
# Config helpers (~/.skillctl/config.yaml)
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    """Load CLI config from ~/.skillctl/config.yaml."""
    config_path = Path.home() / ".skillctl" / "config.yaml"
    if config_path.exists():
        return yaml.safe_load(config_path.read_text()) or {}
    return {}


def _save_config(config: dict):
    """Save CLI config to ~/.skillctl/config.yaml."""
    config_path = Path.home() / ".skillctl" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.dump(config, default_flow_style=False))


def _get_registry_url(args) -> str:
    """Resolve registry URL from args > env > config file."""
    url = getattr(args, "registry_url", None)
    if url:
        return url.rstrip("/")
    url = os.environ.get("SKILLCTL_REGISTRY_URL")
    if url:
        return url.rstrip("/")
    cfg = _load_config()
    url = cfg.get("registry", {}).get("url")
    if url:
        return url.rstrip("/")
    print("Error: No registry URL configured.", file=sys.stderr)
    print("  Fix: Run 'skillctl config set registry.url <url>' or set SKILLCTL_REGISTRY_URL", file=sys.stderr)
    sys.exit(1)


def _get_registry_token(args) -> str | None:
    """Resolve registry token from args > env > config file."""
    token = getattr(args, "token", None)
    if token:
        return token
    token = os.environ.get("SKILLCTL_REGISTRY_TOKEN")
    if token:
        return token
    cfg = _load_config()
    return cfg.get("registry", {}).get("token")


def _load_github_token() -> str | None:
    """Load GitHub token from config file (set by 'skillctl login')."""
    cfg = _load_config()
    return cfg.get("github", {}).get("token")


def main():
    parser = argparse.ArgumentParser(
        prog="skillctl", description="Governance CLI for agent skills"
    )
    sub = parser.add_subparsers(dest="command")

    # skillctl init
    init_p = sub.add_parser("init", help="Create a new skill")
    init_p.add_argument("name", help="Skill name (namespace/skill-name)")

    # skillctl validate
    val_p = sub.add_parser("validate", help="Validate a skill manifest")
    val_p.add_argument(
        "path", nargs="?", default=".", help="Path to skill.yaml or directory"
    )
    val_p.add_argument("--json", action="store_true", help="Output as JSON")
    val_p.add_argument("--strict", action="store_true", help="Treat warnings as errors")

    # skillctl push
    push_p = sub.add_parser("push", help="Push skill to local store")
    push_p.add_argument("path", nargs="?", default=".", help="Path to skill")
    push_p.add_argument(
        "--dry-run", action="store_true", help="Show what would happen"
    )

    # skillctl pull
    pull_p = sub.add_parser("pull", help="Pull skill from local store")
    pull_p.add_argument("ref", help="namespace/name@version")
    pull_p.add_argument("--output", "-o", default=".", help="Output directory")

    # skillctl list
    list_p = sub.add_parser("list", help="List skills in local store")
    list_p.add_argument("--namespace", help="Filter by namespace")
    list_p.add_argument("--tag", help="Filter by tag")
    list_p.add_argument("--json", action="store_true", help="Output as JSON")

    # skillctl version
    sub.add_parser("version", help="Print version info")

    # skillctl diff
    diff_p = sub.add_parser("diff", help="Compare two skill versions")
    diff_p.add_argument("ref_a", help="First ref (namespace/name@version)")
    diff_p.add_argument("ref_b", help="Second ref (namespace/name@version)")
    diff_p.add_argument("--json", action="store_true", help="Output as JSON")

    # skillctl doctor
    sub.add_parser("doctor", help="Diagnose environment issues")

    # skillctl eval <subcommand> — passthrough to eval engine
    eval_p = sub.add_parser("eval", help="Evaluate skills (audit, functional, trigger, report, ...)")
    eval_commands = [
        "audit", "functional", "trigger", "report",
        "snapshot", "regression", "compare", "lifecycle",
    ]

    # skillctl optimize (and subcommands: history, diff)
    register_optimize_commands(sub)

    # skillctl serve
    serve_p = sub.add_parser("serve", help="Start the skill registry server")
    serve_p.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    serve_p.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    serve_p.add_argument("--data-dir", default=None, help="Data directory (default: ~/.skillctl/registry)")
    serve_p.add_argument("--auth-disabled", action="store_true", help="Disable authentication (dev only)")
    serve_p.add_argument("--hmac-key", default=None, help="HMAC key for audit log signing")
    serve_p.add_argument("--log-level", default="info", help="Log level (default: info)")
    serve_p.add_argument("--storage", default="filesystem", choices=["filesystem", "github"],
                         help="Storage backend (default: filesystem)")
    serve_p.add_argument("--github-repo", default=None, help="GitHub repo URL (for github backend)")
    serve_p.add_argument("--github-token", default=None, help="GitHub PAT (for github backend)")
    serve_p.add_argument("--github-branch", default="main", help="GitHub branch (default: main)")

    # skillctl publish
    publish_p = sub.add_parser("publish", help="Publish skill to remote registry")
    publish_p.add_argument("path", nargs="?", default=".", help="Path to skill directory or manifest")
    publish_p.add_argument("--registry-url", default=None, help="Registry URL (overrides config)")
    publish_p.add_argument("--token", default=None, help="Auth token (overrides config)")

    # skillctl search
    search_p = sub.add_parser("search", help="Search remote registry for skills")
    search_p.add_argument("query", nargs="?", default=None, help="Search query")
    search_p.add_argument("--namespace", default=None, help="Filter by namespace")
    search_p.add_argument("--tag", default=None, help="Filter by tag")
    search_p.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    search_p.add_argument("--registry-url", default=None, help="Registry URL (overrides config)")
    search_p.add_argument("--token", default=None, help="Auth token (overrides config)")

    # skillctl token (subcommands)
    token_p = sub.add_parser("token", help="Manage registry API tokens")
    token_sub = token_p.add_subparsers(dest="token_command")
    token_create_p = token_sub.add_parser("create", help="Create a new API token")
    token_create_p.add_argument("--name", required=True, help="Token name")
    token_create_p.add_argument("--scope", action="append", dest="scopes", default=[], help="Permission scope (repeatable)")
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

    args, remaining = parser.parse_known_args()

    try:
        if args.command == "init":
            cmd_init(args)
        elif args.command == "validate":
            cmd_validate(args)
        elif args.command == "push":
            cmd_push(args)
        elif args.command == "pull":
            cmd_pull(args)
        elif args.command == "list":
            cmd_list(args)
        elif args.command == "version":
            cmd_version()
        elif args.command == "diff":
            cmd_diff(args)
        elif args.command == "doctor":
            cmd_doctor(args)
        elif args.command == "eval":
            cmd_eval_passthrough(remaining)
        elif args.command == "optimize":
            handle_optimize(args, remaining)
        elif args.command == "serve":
            cmd_serve(args)
        elif args.command == "publish":
            cmd_publish(args)
        elif args.command == "search":
            cmd_search(args)
        elif args.command == "token":
            cmd_token(args)
        elif args.command == "config":
            cmd_config(args)
        elif args.command == "login":
            cmd_login(args)
        elif args.command == "logout":
            cmd_logout()
        else:
            parser.print_help()
            sys.exit(1)
    except SkillctlError as e:
        print(e.format_human(), file=sys.stderr)
        sys.exit(1)


def cmd_init(args):
    """Scaffold a new skill project."""
    name = args.name
    skill_yaml = (
        f'apiVersion: skillctl.io/v1\n'
        f'kind: Skill\n'
        f'\n'
        f'metadata:\n'
        f'  name: {name}\n'
        f'  version: 0.1.0\n'
        f'  description: ""\n'
        f'\n'
        f'spec:\n'
        f'  content:\n'
        f'    path: ./SKILL.md\n'
        f'  capabilities:\n'
        f'    - read_file\n'
    )
    skill_md = (
        f'# {name.split("/")[-1] if "/" in name else name}\n'
        f'\n'
        f'## Description\n'
        f'\n'
        f'Describe what this skill does.\n'
        f'\n'
        f'## Instructions\n'
        f'\n'
        f'Add skill instructions here.\n'
    )

    Path("skill.yaml").write_text(skill_yaml)
    Path("SKILL.md").write_text(skill_md)
    print(f"✓ Skill scaffolded: skill.yaml + SKILL.md")


def cmd_validate(args):
    """Validate a skill manifest."""
    loader = ManifestLoader()
    validator = SchemaValidator()

    manifest, load_warnings = loader.load(args.path)
    result = validator.validate(manifest)

    # Resolve content for capability check
    base_dir = str(Path(args.path).parent) if Path(args.path).is_file() else args.path
    try:
        content = loader.resolve_content(manifest, base_dir)
    except Exception:
        content = ""

    cap_warnings = validator.check_capabilities(manifest, content)

    # Merge warnings
    all_warnings = []
    for w in load_warnings:
        all_warnings.append(
            {"code": w.code, "message": w.message, "hint": w.hint}
        )
    for w in cap_warnings:
        all_warnings.append(
            {"code": w.code, "message": w.message, "hint": w.hint}
        )

    if getattr(args, "json", False):
        output = {
            "valid": result.valid and len(cap_warnings) == 0,
            "errors": [
                {"code": e.code, "message": e.message, "path": e.path, "hint": e.hint}
                for e in result.errors
            ],
            "warnings": all_warnings,
            "strict": getattr(args, "strict", False),
        }
        print(json.dumps(output, indent=2))
    else:
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

    # Determine exit code
    if result.errors:
        sys.exit(1)
    elif all_warnings and getattr(args, "strict", False):
        sys.exit(1)
    elif all_warnings:
        sys.exit(2)
    else:
        sys.exit(0)


def cmd_push(args):
    """Push a skill to the local store."""
    loader = ManifestLoader()
    validator = SchemaValidator()
    store = ContentStore()

    manifest, warnings = loader.load(args.path)
    result = validator.validate(manifest)
    if not result.valid:
        print("Validation errors — cannot push:", file=sys.stderr)
        for e in result.errors:
            print(f"  ✗ [{e.code}] {e.message}", file=sys.stderr)
        sys.exit(1)

    base_dir = str(Path(args.path).parent) if Path(args.path).is_file() else args.path
    content = loader.resolve_content(manifest, base_dir)

    push_result = store.push(manifest, content.encode(), dry_run=args.dry_run)

    if args.dry_run:
        print(f"Dry run — would push {manifest.metadata.name}@{manifest.metadata.version}")
        print(f"  Hash: {push_result.hash}")
        print(f"  Path: {push_result.path}")
        print(f"  Size: {push_result.size} bytes")
    else:
        print(f"✓ Pushed {manifest.metadata.name}@{manifest.metadata.version}")
        print(f"  Hash: {push_result.hash}")


def cmd_pull(args):
    """Pull a skill from the local store."""
    store = ContentStore()

    # Parse ref: namespace/name@version
    if "@" not in args.ref:
        raise SkillctlError(
            code="E_BAD_REF",
            what=f"Invalid reference: {args.ref}",
            why="Pull requires a name@version reference",
            fix="Use format: namespace/skill-name@1.0.0",
        )

    name, version = args.ref.rsplit("@", 1)
    content, entry = store.pull(name, version)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "SKILL.md"
    output_file.write_bytes(content)
    print(f"✓ Pulled {name}@{version} to {output_file}")
    print(f"  Size: {entry['size']} bytes")
    print(f"  Hash: {entry['hash']}")


def cmd_list(args):
    """List skills in the local store."""
    store = ContentStore()
    entries = store.list_skills(
        namespace=args.namespace, tag=args.tag
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
    import platform
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
        print(f"  ✗ Local store: {store_path} (not found)")
        errors_count += 1

    # 3. Store index is valid JSON
    index_path = Path.home() / ".skillctl" / "index.json"
    if index_path.exists():
        try:
            json.loads(index_path.read_text())
            print(f"  ✓ Store index: valid")
        except (json.JSONDecodeError, OSError):
            print(f"  ✗ Store index: invalid JSON")
            errors_count += 1
    else:
        print(f"  ⚠ Store index: not found (no skills pushed yet)")
        warnings_count += 1

    # 4. Config file exists and is valid YAML
    config_path = Path.home() / ".skillctl" / "config.yaml"
    if config_path.exists():
        try:
            yaml.safe_load(config_path.read_text())
            print(f"  ✓ Config file: {config_path}")
        except yaml.YAMLError:
            print(f"  ✗ Config file: invalid YAML")
            errors_count += 1
    else:
        print(f"  ⚠ Config file: not found")
        warnings_count += 1

    # 5. Registry URL
    cfg = _load_config()
    registry_url = cfg.get("registry", {}).get("url") or os.environ.get("SKILLCTL_REGISTRY_URL")
    if registry_url:
        try:
            req = urllib.request.Request(f"{registry_url.rstrip('/')}/api/v1/health", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                print(f"  ✓ Registry URL: {registry_url} (healthy)")
        except Exception:
            print(f"  ⚠ Registry URL: {registry_url} (unreachable)")
            warnings_count += 1
    else:
        print(f"  ⚠ Registry URL: not configured")
        warnings_count += 1

    # 6. GitHub token
    gh_token = cfg.get("github", {}).get("token")
    if gh_token:
        try:
            req = urllib.request.Request("https://api.github.com/user", method="GET")
            req.add_header("Authorization", f"Bearer {gh_token}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                user = json.loads(resp.read().decode())
                print(f"  ✓ GitHub token: valid ({user.get('login', 'unknown')})")
        except Exception:
            print(f"  ⚠ GitHub token: configured but invalid")
            warnings_count += 1
    else:
        print(f"  ⚠ GitHub token: not configured")
        warnings_count += 1

    # 7. Git installed
    git_path = shutil.which("git")
    if git_path:
        try:
            git_ver = subprocess.check_output(
                ["git", "--version"], stderr=subprocess.DEVNULL, text=True
            ).strip()
            # Extract version number from "git version X.Y.Z"
            git_ver_num = git_ver.replace("git version ", "")
            print(f"  ✓ Git: installed ({git_ver_num})")
        except Exception:
            print(f"  ⚠ Git: found but version check failed")
            warnings_count += 1
    else:
        print(f"  ⚠ Git: not installed")
        warnings_count += 1

    # 8. Required packages
    missing = []
    for pkg in ["fastapi", "uvicorn", "yaml"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"  ✗ Dependencies: missing {', '.join(missing)}")
        errors_count += 1
    else:
        print(f"  ✓ Dependencies: all importable")

    # Summary
    print(f"\n{warnings_count} warnings, {errors_count} errors")
    sys.exit(1 if errors_count > 0 else 0)


def cmd_eval_passthrough(remaining_args: list[str]):
    """Delegate eval commands to skillctl.eval.cli."""
    from skillctl.eval.cli import main as eval_main

    sys.argv = ["skillctl eval"] + remaining_args
    try:
        exit_code = eval_main()
        sys.exit(exit_code or 0)
    except SystemExit as e:
        sys.exit(e.code)


# ---------------------------------------------------------------------------
# 9.1 — skillctl serve
# ---------------------------------------------------------------------------

def cmd_serve(args):
    """Start the skill registry server."""
    import uvicorn
    from skillctl.registry.config import RegistryConfig
    from skillctl.registry.server import create_app

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else None
    config = RegistryConfig(
        host=args.host,
        port=args.port,
        storage_backend=args.storage,
        github_repo=args.github_repo or os.environ.get("SKILLCTL_GITHUB_REPO") or _load_config().get("github", {}).get("repo"),
        github_token=args.github_token or os.environ.get("SKILLCTL_GITHUB_TOKEN") or _load_github_token(),
        github_branch=args.github_branch,
        auth_disabled=args.auth_disabled,
        hmac_key=args.hmac_key,
        log_level=args.log_level,
    )
    if data_dir is not None:
        config.data_dir = data_dir

    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port, log_level=config.log_level)


# ---------------------------------------------------------------------------
# 9.2 — skillctl publish
# ---------------------------------------------------------------------------

def cmd_publish(args):
    """Publish a skill to the remote registry."""
    loader = ManifestLoader()
    validator = SchemaValidator()

    # Load and validate locally
    manifest, load_warnings = loader.load(args.path)
    result = validator.validate(manifest)
    if not result.valid:
        print("Validation errors — cannot publish:", file=sys.stderr)
        for e in result.errors:
            print(f"  ✗ [{e.code}] {e.message}", file=sys.stderr)
        sys.exit(1)

    # Resolve content
    base_dir = str(Path(args.path).parent) if Path(args.path).is_file() else args.path
    content = loader.resolve_content(manifest, base_dir)

    registry_url = _get_registry_url(args)
    token = _get_registry_token(args)

    # Build multipart request body
    boundary = "----skillctl-publish-boundary"
    body = _build_multipart_body(boundary, manifest, content)

    url = f"{registry_url}/api/v1/skills"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            name = data.get("name", manifest.metadata.name)
            version = data.get("version", manifest.metadata.version)
            content_hash = data.get("content_hash", "")
            print(f"✓ Published {name}@{version} to {registry_url}")
            print(f"  Hash: {content_hash}")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        try:
            err = json.loads(body_text)
            print(f"Error: {err.get('what', err.get('detail', body_text))}", file=sys.stderr)
        except json.JSONDecodeError:
            print(f"Error ({e.code}): {body_text}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Could not connect to {registry_url}: {e.reason}", file=sys.stderr)
        sys.exit(1)


def _build_multipart_body(boundary: str, manifest, content: str) -> bytes:
    """Build a multipart/form-data body with manifest JSON and content file."""
    # Convert manifest to dict for JSON serialization
    manifest_dict = {
        "apiVersion": manifest.api_version,
        "kind": manifest.kind,
        "metadata": {
            "name": manifest.metadata.name,
            "version": manifest.metadata.version,
            "description": manifest.metadata.description,
            "tags": manifest.metadata.tags,
            "authors": [
                {"name": a.name, **({"email": a.email} if a.email else {})}
                for a in manifest.metadata.authors
            ],
        },
        "spec": {
            "content": {},
            "capabilities": manifest.spec.capabilities,
        },
    }
    if manifest.metadata.license:
        manifest_dict["metadata"]["license"] = manifest.metadata.license
    if manifest.spec.content.path:
        manifest_dict["spec"]["content"]["path"] = manifest.spec.content.path
    if manifest.spec.content.inline:
        manifest_dict["spec"]["content"]["inline"] = manifest.spec.content.inline

    parts = []
    # Part 1: manifest JSON
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="manifest"\r\n'
        f"Content-Type: application/json\r\n"
        f"\r\n"
        f"{json.dumps(manifest_dict)}\r\n"
    )
    # Part 2: content file
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="content"; filename="SKILL.md"\r\n'
        f"Content-Type: application/octet-stream\r\n"
        f"\r\n"
    )
    body = "".join(parts).encode()
    body += content.encode()
    body += f"\r\n--{boundary}--\r\n".encode()
    return body


# ---------------------------------------------------------------------------
# 9.3 — skillctl search
# ---------------------------------------------------------------------------

def cmd_search(args):
    """Search the remote registry for skills."""
    registry_url = _get_registry_url(args)
    token = _get_registry_token(args)

    params = []
    if args.query:
        params.append(f"q={urllib.request.quote(args.query)}")
    if args.namespace:
        params.append(f"namespace={urllib.request.quote(args.namespace)}")
    if args.tag:
        params.append(f"tag={urllib.request.quote(args.tag)}")
    params.append(f"limit={args.limit}")

    url = f"{registry_url}/api/v1/skills"
    if params:
        url += "?" + "&".join(params)

    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        print(f"Error ({e.code}): {body_text}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Could not connect to {registry_url}: {e.reason}", file=sys.stderr)
        sys.exit(1)

    skills = data.get("skills", [])
    total = data.get("total", len(skills))

    if not skills:
        print("No skills found.")
        return

    # Table header
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


# ---------------------------------------------------------------------------
# 9.4 — skillctl token create
# ---------------------------------------------------------------------------

def cmd_token(args):
    """Manage registry API tokens."""
    if args.token_command == "create":
        cmd_token_create(args)
    else:
        print("Usage: skillctl token create --name <name> --scope <scope>", file=sys.stderr)
        sys.exit(1)


def cmd_token_create(args):
    """Create a new API token on the remote registry."""
    registry_url = _get_registry_url(args)
    token = _get_registry_token(args)

    payload = {
        "name": args.name,
        "permissions": args.scopes if args.scopes else ["read"],
    }
    if args.expires:
        # Parse duration like "90d" into days
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
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            raw_token = data.get("token", "")
            print(raw_token)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        try:
            err = json.loads(body_text)
            print(f"Error: {err.get('what', err.get('detail', body_text))}", file=sys.stderr)
        except json.JSONDecodeError:
            print(f"Error ({e.code}): {body_text}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: Could not connect to {registry_url}: {e.reason}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# 9.5 — skillctl config
# ---------------------------------------------------------------------------

_SUPPORTED_CONFIG_KEYS = {"registry.url", "registry.token", "github.client_id", "github.token", "github.repo"}


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
    """Set a config value."""
    key = args.key
    value = args.value

    if key not in _SUPPORTED_CONFIG_KEYS:
        print(f"Error: Unknown config key '{key}'.", file=sys.stderr)
        print(f"  Supported keys: {', '.join(sorted(_SUPPORTED_CONFIG_KEYS))}", file=sys.stderr)
        sys.exit(1)

    config = _load_config()
    parts = key.split(".")
    # Navigate/create nested dict
    d = config
    for part in parts[:-1]:
        if part not in d or not isinstance(d[part], dict):
            d[part] = {}
        d = d[part]
    d[parts[-1]] = value

    _save_config(config)
    print(f"✓ Set {key} = {value}")


def cmd_config_get(args):
    """Get a config value."""
    key = args.key

    if key not in _SUPPORTED_CONFIG_KEYS:
        print(f"Error: Unknown config key '{key}'.", file=sys.stderr)
        print(f"  Supported keys: {', '.join(sorted(_SUPPORTED_CONFIG_KEYS))}", file=sys.stderr)
        sys.exit(1)

    config = _load_config()
    parts = key.split(".")
    d = config
    for part in parts:
        if isinstance(d, dict) and part in d:
            d = d[part]
        else:
            print(f"{key}: (not set)")
            return
    print(f"{key}: {d}")


# ---------------------------------------------------------------------------
# skillctl login / logout — GitHub Device Flow
# ---------------------------------------------------------------------------

def cmd_login(args):
    """Authenticate with GitHub using the device flow."""
    from skillctl.github_auth import (
        get_client_id, device_flow_login, verify_token, save_github_token,
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

    # Verify and show who we authenticated as
    user = verify_token(token)
    save_github_token(token)

    print(f"\n✓ Authenticated as {user.get('login', 'unknown')} ({user.get('name', '')})")
    print(f"  Token saved to ~/.skillctl/config.yaml")


def cmd_logout():
    """Remove stored GitHub credentials."""
    config_path = Path.home() / ".skillctl" / "config.yaml"
    if not config_path.exists():
        print("Not logged in.")
        return

    import yaml
    cfg = yaml.safe_load(config_path.read_text()) or {}
    gh = cfg.get("github", {})
    if "token" not in gh:
        print("Not logged in.")
        return

    del gh["token"]
    if not gh:
        del cfg["github"]
    config_path.write_text(yaml.dump(cfg, default_flow_style=False))
    print("✓ GitHub credentials removed.")
