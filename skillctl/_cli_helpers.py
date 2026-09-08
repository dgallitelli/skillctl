"""Private helpers for ``skillctl.cli``.

This module exists to keep ``skillctl/cli.py`` focused on parser /
dispatch / handlers without also being the home of registry-HTTP
boilerplate, config IO, and breadcrumb logic.

The leading underscore signals "private — do not import from outside
the skillctl package."  Tests that need to monkeypatch helpers should
do so via ``skillctl.cli`` (the helpers are re-exported there for
test stability).

**Important caveat for monkeypatching**: helper-to-helper calls *inside
this module* (for example ``_require_registry_url`` calling
``_get_registry_url``) resolve against this module's own globals, NOT
against ``skillctl.cli``.  So a test that does
``monkeypatch.setattr("skillctl.cli._get_registry_url", ...)`` will only
intercept callers that go through ``skillctl.cli`` — not callers nested
inside ``_cli_helpers.py``.  Today only ``cmd_apply`` reads
``_get_registry_url`` directly, so this is fine; if that ever changes,
patch ``skillctl._cli_helpers._get_registry_url`` instead (or both).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from skillctl.config import (
    CONFIG_PATH,
    load_config as _load_skillctl_config,
)
from skillctl.errors import SkillctlError

if TYPE_CHECKING:
    from skillctl.store import PushResult


# ---------------------------------------------------------------------------
# Config helpers — thin wrappers over skillctl.config
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    """Load raw CLI config as a dict (backward compat for config set/get)."""
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return {}


def _save_config(config: dict):
    """Save raw CLI config dict (backward compat for config set/get)."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.dump(config, default_flow_style=False))
    CONFIG_PATH.chmod(0o600)


# ---------------------------------------------------------------------------
# Registry URL / token resolution
# ---------------------------------------------------------------------------


def _get_registry_url(args) -> str | None:
    """Resolve registry URL from args > env > typed config."""
    url = getattr(args, "registry_url", None)
    if url:
        return url.rstrip("/")
    url = os.environ.get("SKILLCTL_REGISTRY_URL")
    if url:
        return url.rstrip("/")
    cfg = _load_skillctl_config()
    url = cfg.registry.local.url
    if url:
        return url.rstrip("/")
    return None


def _require_registry_url(args) -> str:
    """Resolve registry URL, raise SkillctlError if not configured."""
    url = _get_registry_url(args)
    if url:
        return url
    raise SkillctlError(
        code="E_NO_REGISTRY",
        what="No registry URL configured",
        why="This command requires a registry URL to communicate with the remote registry",
        fix="Run 'skillctl configure' or 'skillctl config set registry.url <url>'",
    )


def _get_registry_token(args) -> str | None:
    """Resolve registry token from args > env > typed config."""
    token = getattr(args, "token", None)
    if token:
        return token
    token = os.environ.get("SKILLCTL_REGISTRY_TOKEN")
    if token:
        return token
    cfg = _load_skillctl_config()
    return cfg.registry.local.token


def _load_github_token() -> str | None:
    """Load GitHub token from env > config."""
    from skillctl.github_auth import load_github_token

    return load_github_token()


# ---------------------------------------------------------------------------
# Output / TTY discipline
# ---------------------------------------------------------------------------


def _output_is_machine(args) -> bool:
    """True when output is being piped or the caller asked for machine output.

    Suppress human breadcrumbs in those cases so they don't pollute JSON
    pipelines or CI logs.

    A user is asking for machine output when:
    - ``--json`` is passed (legacy boolean flag),
    - ``--quiet`` is passed,
    - ``--format`` is anything other than ``text`` (e.g. ``json``,
      ``html``, ``github``), or
    - stdout is not a TTY.
    """
    if getattr(args, "json", False):
        return True
    if getattr(args, "quiet", False):
        return True
    fmt = getattr(args, "format", None)
    if fmt and fmt != "text":
        return True
    return not sys.stdout.isatty()


def _emit_plugin_hint():
    """Emit a Claude Code plugin hint on stderr when running inside Claude Code."""
    if os.environ.get("CLAUDECODE"):
        print(
            '<claude-code-hint v="1" type="plugin" value="skillctl@claude-plugins-official" />',
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Registry HTTP helper — collapses six near-duplicate try/except blocks
# ---------------------------------------------------------------------------


def _registry_request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: int = 30,
) -> bytes:
    """Issue an HTTP request to the registry and return the response body.

    On non-2xx responses or transport failures, raises ``SkillctlError``
    with a code the dispatch layer's ``except SkillctlError`` handler
    formats and prints.  The bytes returned are the raw response body —
    the caller decodes / parses them as JSON if appropriate.

    Codes raised:

    - ``E_REGISTRY_HTTP``        — non-2xx response (response body included).
    - ``E_REGISTRY_UNREACHABLE`` — TCP/DNS/timeout failure.
    """
    req = urllib.request.Request(url, data=body, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        # If the registry returned a structured error envelope, surface
        # its `what` field — the audit/api layer emits these with
        # SkillctlError-shaped JSON.
        what = f"Registry request failed (HTTP {e.code})"
        if body_text:
            try:
                envelope = json.loads(body_text)
            except (json.JSONDecodeError, ValueError):
                envelope = None
            if isinstance(envelope, dict):
                detail = envelope.get("detail")
                if isinstance(detail, dict) and detail.get("what"):
                    what = f"{what}: {detail['what']}"
                elif envelope.get("what"):
                    what = f"{what}: {envelope['what']}"
        raise SkillctlError(
            code="E_REGISTRY_HTTP",
            what=what,
            why=body_text or e.reason or f"HTTP {e.code}",
            fix="Check registry URL and auth with 'skillctl doctor'",
        ) from e
    except urllib.error.URLError as e:
        raise SkillctlError(
            code="E_REGISTRY_UNREACHABLE",
            what=f"Could not connect to {url}",
            why=str(e.reason),
            fix="Check registry URL with 'skillctl doctor'",
        ) from e


# ---------------------------------------------------------------------------
# `apply_skill` — library form of the CLI's `apply` lifecycle
# ---------------------------------------------------------------------------


@dataclass
class ApplyResult:
    """Outcome of an :func:`apply_skill` call.

    Attributes:
        ref: Canonical ``namespace/name@version`` reference.
        local_status: ``"pushed"`` (new push to local store), ``"unchanged"``
            (already existed), or ``"dry-run"`` (when called with
            ``dry_run=True``).
        remote_status: ``None`` (no remote published — either ``--local``,
            no registry configured, or dry-run), ``"published"``,
            ``"blocked (security)"`` if the security gate fired, or
            ``"failed (...)"`` with the failure reason.
        push_result: The store-level ``PushResult`` (hash, size, created
            flag).  ``None`` on dry-run.
        critical_findings: Non-empty list of CRITICAL findings ONLY when
            ``remote_status == "blocked (security)"``; otherwise empty.
            Lets the CLI shim format the per-finding stderr output.
    """

    ref: str
    local_status: str
    remote_status: str | None
    push_result: "PushResult | None"
    critical_findings: list = field(default_factory=list)


def apply_skill(
    path: str,
    *,
    dry_run: bool = False,
    local: bool = False,
    registry_url: str | None = None,
    token: str | None = None,
) -> ApplyResult:
    """Library form of the ``skillctl apply`` lifecycle.

    Validates the skill at *path*, pushes it to the local content-
    addressed store (idempotently), and — when a registry is configured
    AND *local* is False — publishes to the remote after a security
    gate.  No CLI side effects: doesn't print, doesn't ``sys.exit``.

    The CLI shim ``cmd_apply`` calls this and formats the result; so
    does ``cmd_install`` when the user passes a local path or a URL
    (via ``--from-url``).

    Args:
        path: Path to the skill directory or manifest file.
        dry_run: If True, validate + report what would happen without
            mutating the store.
        local: If True, skip remote publish even when a registry URL is
            configured.
        registry_url: Override of the registry URL.  ``None`` falls back
            to the resolved-from-config value (via :func:`_get_registry_url`
            on a synthetic argparse-shaped object — internal detail).
        token: Override of the registry auth token.  ``None`` falls back
            to config.

    Returns:
        :class:`ApplyResult` describing what happened.

    Raises:
        SkillctlError(code="E_VALIDATION"): The manifest failed schema
            validation.  ``why`` is a multi-line list of ``[CODE]
            message`` entries — the CLI shim ``cmd_apply`` catches
            ``E_VALIDATION`` and re-formats inline so user output is
            unchanged from before the refactor.
        SkillctlError(code="E_NO_NAMESPACE"): Bare-name skill being
            published to a remote (the shared-namespace gate).
    """
    # Local imports so this module stays cheap to import; the
    # heavy-weight ``ManifestLoader`` etc. are only loaded when a
    # caller actually applies a skill.  Resolve ``ContentStore`` via
    # ``skillctl.cli`` so test ``monkeypatch.setattr("skillctl.cli.
    # ContentStore", ...)`` patches are honoured at call time.
    #
    # WARNING: ``skillctl.cli`` imports ``skillctl._cli_helpers`` at
    # module top.  This local import works because by the time
    # ``apply_skill`` is actually *called*, ``cli``'s own
    # initialisation is finished.  Don't move this import to module
    # scope — that creates a hard circular import — and don't call
    # ``apply_skill`` from anything that runs during ``skillctl.cli``
    # module initialisation.
    from skillctl import cli as _cli
    from skillctl.artifact import build_artifact
    from skillctl.manifest import ManifestLoader
    from skillctl.validator import SchemaValidator

    loader = ManifestLoader()
    validator = SchemaValidator()
    store = _cli.ContentStore()

    # 1. Load manifest
    manifest, _warnings = loader.load(path)

    # 2. Validate.  On failure raise E_VALIDATION carrying the
    # structured errors as an instance attribute.  The CLI shim
    # ``cmd_apply`` reads ``err.errors`` to print the inline
    # per-error format (same shape as pre-refactor) without doing a
    # string round-trip.  ``why`` carries a flattened text fallback
    # for any caller that doesn't dig into the attribute.
    result = validator.validate(manifest)
    if not result.valid:
        why_lines = [f"  [{e.code}] {e.message}" for e in result.errors]
        err = SkillctlError(
            code="E_VALIDATION",
            what="Skill manifest failed validation",
            why="\n".join(why_lines),
            fix="Address the validation errors above and retry.",
        )
        # Carry the original ValidationIssue list so cmd_apply can
        # render it without re-parsing the multi-line ``why``.
        err.errors = result.errors  # type: ignore[attr-defined]
        raise err

    # 2b. Namespace gate — bare names allowed locally, blocked from
    # the remote.  Resolve registry-config via the cli module so
    # tests can patch ``skillctl.cli._get_registry_url``.
    bare_name = "/" not in manifest.metadata.name
    args_shim = _ArgsShim(registry_url=registry_url, token=token)
    going_remote = bool(_cli._get_registry_url(args_shim)) and not local
    if bare_name and going_remote:
        raise SkillctlError(
            code="E_NO_NAMESPACE",
            what=f"Skill '{manifest.metadata.name}' has no namespace",
            why="The remote registry requires namespaced names to prevent collisions",
            fix=(
                "Add a 'skillctl:' block with 'namespace: my-org' to SKILL.md frontmatter,\n"
                "  or create a skill.yaml with 'metadata.name: my-org/<skill>',\n"
                "  or pass --local to push to the local store only."
            ),
        )

    # 3. Resolve content
    base_dir = str(Path(path).parent) if Path(path).is_file() else path
    content = loader.resolve_content(manifest, base_dir)
    content_bytes = content.encode()
    artifact = build_artifact(path, manifest, content_bytes)
    ref = f"{manifest.metadata.name}@{manifest.metadata.version}"

    if dry_run:
        push_result = store.push(manifest, content_bytes, dry_run=True, artifact=artifact)
        return ApplyResult(
            ref=ref,
            local_status="dry-run",
            remote_status=None,
            push_result=push_result,
        )

    # 4. Push to local store (idempotent)
    push_result: "PushResult | None" = None
    try:
        push_result = store.push(manifest, content_bytes, artifact=artifact)
        local_status = "pushed"
    except SkillctlError as e:
        if e.code == "E_ALREADY_EXISTS":
            local_status = "unchanged"
        else:
            raise

    # 5. Optionally publish to remote (with security gate)
    remote_status: str | None = None
    critical_findings: list = []
    resolved_registry = _cli._get_registry_url(args_shim)
    if resolved_registry and not local:
        from skillctl.eval.audit.security_scan import scan_security
        from skillctl.eval.schemas import Severity

        scan_path = str(Path(path).parent) if Path(path).is_file() else path
        findings = scan_security(scan_path)
        critical_findings = [f for f in findings if f.severity == Severity.CRITICAL]
        if critical_findings:
            remote_status = "blocked (security)"
        else:
            try:
                _cli._publish_to_registry(args_shim, manifest, content, resolved_registry, artifact=artifact)
                remote_status = "published"
            except Exception as e:  # noqa: BLE001 — preserve old behaviour
                remote_status = f"failed ({e})"

    return ApplyResult(
        ref=ref,
        local_status=local_status,
        remote_status=remote_status,
        push_result=push_result,
        critical_findings=critical_findings,
    )


@dataclass
class _ArgsShim:
    """Minimal stand-in for an ``argparse.Namespace`` so the existing
    config-resolution helpers can read fields via ``getattr`` without
    ``apply_skill`` constructing fake CLI args.

    **Attribute contract** (must stay in sync with the helpers):

    - ``registry_url``: read by :func:`_get_registry_url` (line 72).
    - ``token``: read by :func:`_get_registry_token` (line 100).

    No other attributes are read by ``_publish_to_registry`` /
    ``_get_registry_url`` / ``_get_registry_token`` today.  If any of
    those helpers grow to read another args attribute, add it here —
    otherwise ``getattr(shim, "<new_attr>", None)`` will silently
    return ``None`` and ``apply_skill`` will misbehave.
    """

    registry_url: str | None = None
    token: str | None = None
