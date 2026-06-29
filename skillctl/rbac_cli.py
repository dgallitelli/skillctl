"""CLI commands for RBAC: ``skillctl auth``, ``skillctl rbac``, ``skillctl namespace``.

Talks to the registry's RBAC endpoints over HTTP (stdlib urllib, no new deps)
and stores session tokens via :mod:`skillctl.credentials`.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from skillctl import credentials
from skillctl.errors import SkillctlError


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _request(
    method: str,
    url: str,
    *,
    token: Optional[str] = None,
    json_body: Optional[dict] = None,
    timeout: int = 15,
) -> tuple[int, dict]:
    """Perform a JSON HTTP request. Returns ``(status_code, parsed_body)``."""
    data = None
    headers = {"Accept": "application/json"}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            parsed = {"detail": body}
        return e.code, parsed
    except urllib.error.URLError as e:
        raise SkillctlError(
            code="E_REGISTRY_UNREACHABLE",
            what=f"Could not connect to {url}",
            why=str(e.reason),
            fix="Check the registry URL and that the server is running.",
        ) from e


def _detail_message(body: dict) -> str:
    detail = body.get("detail", body)
    if isinstance(detail, dict):
        return detail.get("why") or detail.get("what") or json.dumps(detail)
    return str(detail)


def _resolve_registry(args, *, required: bool = True) -> Optional[str]:
    url = getattr(args, "registry", None) or os.environ.get("SKILLCTL_REGISTRY_URL")
    if not url:
        regs = credentials.list_registries()
        if len(regs) == 1:
            url = next(iter(regs))
        else:
            try:
                from skillctl.config import load_config

                cfg = load_config()
                url = getattr(getattr(cfg.registry, "local", None), "url", None)
            except Exception:
                url = None
    if not url and required:
        raise SkillctlError(
            code="E_NO_REGISTRY",
            what="No registry URL configured",
            why="Pass --registry, set SKILLCTL_REGISTRY_URL, or log in first",
            fix="skillctl auth login --registry <url> --username <u> --password <p>",
        )
    return url.rstrip("/") if url else None


def _token_for(url: str) -> Optional[str]:
    return os.environ.get("SKILLCTL_REGISTRY_TOKEN") or credentials.get_token(url)


def _parse_expires(value: Optional[str]) -> Optional[int]:
    """Parse an expiry like '90d' into a number of days."""
    if not value:
        return None
    v = value.strip().lower()
    if v.endswith("d"):
        v = v[:-1]
    try:
        return int(v)
    except ValueError as exc:
        raise SkillctlError(
            code="E_BAD_EXPIRY",
            what=f"Invalid expiry {value!r}",
            why="Expected a number of days like '90d' or '30'",
            fix="Use a value like --expires 90d",
        ) from exc


# ---------------------------------------------------------------------------
# auth commands
# ---------------------------------------------------------------------------


def cmd_auth_login(args) -> int:
    url = _resolve_registry(args)
    status, body = _request(
        "POST",
        f"{url}/api/v1/auth/login",
        json_body={"username": args.username, "password": args.password},
    )
    if status != 200:
        print(f"Login failed: {_detail_message(body)}", file=sys.stderr)
        return 1
    credentials.save_credentials(url, body["token"], body["username"], body.get("expires_at"))
    roles = ", ".join(body.get("roles", [])) or "(none)"
    print(f"✓ Logged in to {url} as {body['username']} (roles: {roles})")
    return 0


def cmd_auth_logout(args) -> int:
    url = _resolve_registry(args)
    if credentials.clear_credentials(url):
        print(f"✓ Logged out of {url}")
    else:
        print(f"No stored credentials for {url}")
    return 0


def cmd_auth_whoami(args) -> int:
    url = _resolve_registry(args)
    token = _token_for(url)
    if not token:
        print(f"Not logged in to {url}. Run: skillctl auth login --registry {url} ...", file=sys.stderr)
        return 1
    status, body = _request("GET", f"{url}/api/v1/auth/whoami", token=token)
    if status != 200:
        print(f"whoami failed: {_detail_message(body)}", file=sys.stderr)
        return 1
    roles = ", ".join(body.get("roles", [])) or "(none)"
    namespaces = ", ".join(body.get("namespaces", [])) or "(none)"
    print(f"  User:       {body['username']}")
    print(f"  Roles:      {roles}")
    print(f"  Namespaces: {namespaces}")
    print(f"  Token:      {body.get('token_id') or '-'} (expires: {body.get('token_expires_at') or 'never'})")
    print(f"  Registry:   {url}")
    return 0


def cmd_auth_token_create(args) -> int:
    url = _resolve_registry(args)
    token = _token_for(url)
    scopes = [s.strip() for s in (args.scope or "*").split(",") if s.strip()] or ["*"]
    status, body = _request(
        "POST",
        f"{url}/api/v1/auth/tokens",
        token=token,
        json_body={"name": args.name, "scopes": scopes, "expires_in_days": _parse_expires(args.expires)},
    )
    if status != 201:
        print(f"Token creation failed: {_detail_message(body)}", file=sys.stderr)
        return 1
    print(f"Token created: {body['token']}")
    print("(save this — it won't be shown again)")
    return 0


def cmd_auth_token_list(args) -> int:
    url = _resolve_registry(args)
    token = _token_for(url)
    status, body = _request("GET", f"{url}/api/v1/auth/tokens", token=token)
    if status != 200:
        print(f"Listing failed: {_detail_message(body)}", file=sys.stderr)
        return 1
    toks = body.get("tokens", [])
    if not toks:
        print("No tokens.")
        return 0
    print(f"{'NAME':<20} {'SCOPES':<24} {'EXPIRES':<22} STATUS")
    for t in toks:
        scopes = ",".join(t.get("scopes") or [])
        status_s = "revoked" if t.get("revoked") else "active"
        print(f"{t['name']:<20} {scopes:<24} {str(t.get('expires_at') or 'never'):<22} {status_s}")
    return 0


def cmd_auth_token_revoke(args) -> int:
    url = _resolve_registry(args)
    token = _token_for(url)
    # Resolve token name → id via the list endpoint.
    status, body = _request("GET", f"{url}/api/v1/auth/tokens", token=token)
    if status != 200:
        print(f"Revoke failed: {_detail_message(body)}", file=sys.stderr)
        return 1
    target = next((t for t in body.get("tokens", []) if t["name"] == args.name and not t.get("revoked")), None)
    if target is None:
        print(f"No active token named {args.name!r}.", file=sys.stderr)
        return 1
    st, rb = _request("DELETE", f"{url}/api/v1/tokens/{target['token_id']}", token=token)
    if st not in (200, 204):
        print(f"Revoke failed: {_detail_message(rb)}", file=sys.stderr)
        return 1
    print(f"✓ Revoked token {args.name!r}")
    return 0


def cmd_auth_change_password(args) -> int:
    url = _resolve_registry(args)
    token = _token_for(url)
    if not token:
        print(f"Not logged in to {url}.", file=sys.stderr)
        return 1
    status, body = _request(
        "POST",
        f"{url}/api/v1/auth/change-password",
        token=token,
        json_body={"old_password": args.old, "new_password": args.new},
    )
    if status != 200:
        print(f"Password change failed: {_detail_message(body)}", file=sys.stderr)
        return 1
    print("✓ Password changed")
    return 0


# ---------------------------------------------------------------------------
# rbac commands
# ---------------------------------------------------------------------------


def cmd_rbac_assign(args) -> int:
    url = _resolve_registry(args)
    token = _token_for(url)
    status, body = _request(
        "POST",
        f"{url}/api/v1/rbac/assign",
        token=token,
        json_body={"username": args.user, "role": args.role, "namespace": args.namespace},
    )
    if status != 201:
        print(f"Assign failed: {_detail_message(body)}", file=sys.stderr)
        return 1
    print(f"✓ Assigned {args.role} to {args.user} in {args.namespace}")
    return 0


def cmd_rbac_revoke(args) -> int:
    url = _resolve_registry(args)
    token = _token_for(url)
    status, body = _request(
        "POST",
        f"{url}/api/v1/rbac/revoke",
        token=token,
        json_body={"username": args.user, "role": args.role, "namespace": args.namespace},
    )
    if status != 200:
        print(f"Revoke failed: {_detail_message(body)}", file=sys.stderr)
        return 1
    print(f"✓ Revoked {args.role} from {args.user} in {args.namespace}")
    return 0


def cmd_rbac_list(args) -> int:
    url = _resolve_registry(args)
    token = _token_for(url)
    status, body = _request(
        "GET", f"{url}/api/v1/rbac/assignments?username={urllib.parse.quote(args.user)}", token=token
    )
    if status != 200:
        print(f"List failed: {_detail_message(body)}", file=sys.stderr)
        return 1
    assigns = body.get("assignments", [])
    if not assigns:
        print(f"No role assignments for {args.user}.")
        return 0
    print(f"Role assignments for {args.user}:")
    for a in assigns:
        print(f"  {a['role']:<12} {a['namespace']}")
    return 0


def cmd_rbac_check(args) -> int:
    url = _resolve_registry(args)
    token = _token_for(url)
    status, body = _request(
        "POST",
        f"{url}/api/v1/rbac/check",
        token=token,
        json_body={"username": args.user, "permission": args.permission, "namespace": args.namespace},
    )
    if status != 200:
        print(f"Check failed: {_detail_message(body)}", file=sys.stderr)
        return 1
    if body.get("allowed"):
        print(f"✓ ALLOWED — {body.get('reason')}")
        return 0
    print(f"✗ DENIED — {body.get('reason')}")
    return 1


# ---------------------------------------------------------------------------
# namespace commands
# ---------------------------------------------------------------------------


def cmd_namespace_create(args) -> int:
    url = _resolve_registry(args)
    token = _token_for(url)
    status, body = _request(
        "POST",
        f"{url}/api/v1/namespaces",
        token=token,
        json_body={"path": args.path, "description": args.description or ""},
    )
    if status != 201:
        print(f"Create failed: {_detail_message(body)}", file=sys.stderr)
        return 1
    print(f"✓ Created namespace {args.path}")
    return 0


def cmd_namespace_list(args) -> int:
    url = _resolve_registry(args)
    token = _token_for(url)
    status, body = _request("GET", f"{url}/api/v1/namespaces", token=token)
    if status != 200:
        print(f"List failed: {_detail_message(body)}", file=sys.stderr)
        return 1
    namespaces = body.get("namespaces", [])
    if not namespaces:
        print("No namespaces accessible.")
        return 0
    for ns in namespaces:
        desc = f"  — {ns['description']}" if ns.get("description") else ""
        print(f"  {ns['path']}{desc}")
    return 0


def cmd_namespace_grant(args) -> int:
    """Shorthand for rbac assign within a namespace context."""
    args.user = args.user
    return cmd_rbac_assign(args)


# ---------------------------------------------------------------------------
# Parser registration + dispatch
# ---------------------------------------------------------------------------


def _add_registry_flag(p):
    p.add_argument("--registry", default=None, help="Registry URL (overrides stored/config)")


def register_rbac_commands(sub) -> None:
    # auth
    auth_p = sub.add_parser("auth", help="Authenticate and manage tokens")
    auth_sub = auth_p.add_subparsers(dest="auth_command")

    login = auth_sub.add_parser("login", help="Authenticate and store a token")
    login.add_argument("--username", required=True)
    login.add_argument("--password", required=True)
    _add_registry_flag(login)

    logout = auth_sub.add_parser("logout", help="Remove stored credentials")
    _add_registry_flag(logout)

    whoami = auth_sub.add_parser("whoami", help="Show current identity")
    _add_registry_flag(whoami)

    chpw = auth_sub.add_parser("change-password", help="Change your password")
    chpw.add_argument("--old", required=True)
    chpw.add_argument("--new", required=True)
    _add_registry_flag(chpw)

    token_p = auth_sub.add_parser("token", help="Manage scoped tokens")
    token_sub = token_p.add_subparsers(dest="token_command")
    tc = token_sub.add_parser("create", help="Create a scoped token")
    tc.add_argument("--name", required=True)
    tc.add_argument("--scope", default="*", help="Comma-separated namespace scopes (default: *)")
    tc.add_argument("--expires", default=None, help="Expiry like 90d")
    _add_registry_flag(tc)
    tl = token_sub.add_parser("list", help="List your tokens")
    _add_registry_flag(tl)
    tr = token_sub.add_parser("revoke", help="Revoke a token by name")
    tr.add_argument("--name", required=True)
    _add_registry_flag(tr)

    # rbac
    rbac_p = sub.add_parser("rbac", help="Manage roles (admin)")
    rbac_sub = rbac_p.add_subparsers(dest="rbac_command")
    for verb in ("assign", "revoke"):
        rp = rbac_sub.add_parser(verb, help=f"{verb.capitalize()} a role")
        rp.add_argument("--user", required=True)
        rp.add_argument("--role", required=True)
        rp.add_argument("--namespace", required=True)
        _add_registry_flag(rp)
    rl = rbac_sub.add_parser("list", help="List a user's role assignments")
    rl.add_argument("--user", required=True)
    _add_registry_flag(rl)
    rc = rbac_sub.add_parser("check", help="Dry-run a permission check")
    rc.add_argument("--user", required=True)
    rc.add_argument("--permission", required=True)
    rc.add_argument("--namespace", required=True)
    _add_registry_flag(rc)

    # namespace
    ns_p = sub.add_parser("namespace", help="Manage namespaces")
    ns_sub = ns_p.add_subparsers(dest="namespace_command")
    nc = ns_sub.add_parser("create", help="Create a namespace")
    nc.add_argument("path")
    nc.add_argument("--description", default="")
    _add_registry_flag(nc)
    nl = ns_sub.add_parser("list", help="List accessible namespaces")
    _add_registry_flag(nl)
    ng = ns_sub.add_parser("grant", help="Assign a role within a namespace (alias for rbac assign)")
    ng.add_argument("--namespace", required=True)
    ng.add_argument("--user", required=True)
    ng.add_argument("--role", required=True)
    _add_registry_flag(ng)


_AUTH_DISPATCH = {
    "login": cmd_auth_login,
    "logout": cmd_auth_logout,
    "whoami": cmd_auth_whoami,
    "change-password": cmd_auth_change_password,
}
_TOKEN_DISPATCH = {
    "create": cmd_auth_token_create,
    "list": cmd_auth_token_list,
    "revoke": cmd_auth_token_revoke,
}
_RBAC_DISPATCH = {
    "assign": cmd_rbac_assign,
    "revoke": cmd_rbac_revoke,
    "list": cmd_rbac_list,
    "check": cmd_rbac_check,
}
_NS_DISPATCH = {
    "create": cmd_namespace_create,
    "list": cmd_namespace_list,
    "grant": cmd_namespace_grant,
}


def dispatch_auth(args) -> int:
    if args.auth_command == "token":
        handler = _TOKEN_DISPATCH.get(args.token_command)
        if handler is None:
            print("Usage: skillctl auth token {create|list|revoke}", file=sys.stderr)
            return 1
        return handler(args)
    handler = _AUTH_DISPATCH.get(args.auth_command)
    if handler is None:
        print("Usage: skillctl auth {login|logout|whoami|token}", file=sys.stderr)
        return 1
    return handler(args)


def dispatch_rbac(args) -> int:
    handler = _RBAC_DISPATCH.get(args.rbac_command)
    if handler is None:
        print("Usage: skillctl rbac {assign|revoke|list|check}", file=sys.stderr)
        return 1
    return handler(args)


def dispatch_namespace(args) -> int:
    handler = _NS_DISPATCH.get(args.namespace_command)
    if handler is None:
        print("Usage: skillctl namespace {create|list|grant}", file=sys.stderr)
        return 1
    return handler(args)
