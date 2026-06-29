"""Policy configuration loader.

Builds a :class:`PolicyEngine` from a YAML file (default
``.skillctl/policies.yaml``). Reuses the ``.skillctl`` config home rather than
introducing a separate ``.skillsops`` directory.

Config shape::

    policies:
      - name: rate-limit
        type: builtin.rate_limit
        config: {max_per_minute: 30, scope: actor}
    observability:
      enabled: true
      exporter: otlp
      endpoint: http://otel-collector:4317
    logging:
      level: INFO
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from skillctl.policy.builtin.data_boundary import DataBoundaryHook
from skillctl.policy.builtin.output_size import OutputSizeHook
from skillctl.policy.builtin.pii_redaction import PIIRedactionHook
from skillctl.policy.builtin.rate_limit import RateLimitHook
from skillctl.policy.builtin.time_window import TimeWindowHook
from skillctl.policy.engine import PolicyEngine
from skillctl.policy.external.cedar import CedarHook
from skillctl.policy.external.opa import OPAHook
from skillctl.policy.store import PolicyStore

DEFAULT_CONFIG_PATH = Path(".skillctl/policies.yaml")
DEFAULT_STORE_PATH = Path.home() / ".skillctl" / "policy.db"

# type string → (hook class, needs_store)
_HOOK_TYPES = {
    "builtin.rate_limit": (RateLimitHook, True),
    "builtin.data_boundary": (DataBoundaryHook, False),
    "builtin.pii_redaction": (PIIRedactionHook, False),
    "builtin.time_window": (TimeWindowHook, False),
    "builtin.output_size": (OutputSizeHook, False),
    "external.opa": (OPAHook, False),
    "external.cedar": (CedarHook, False),
}


class PolicyConfigError(ValueError):
    """Raised when a policy configuration is invalid."""


@dataclass
class ParsedPolicy:
    name: str
    type: str
    config: dict = field(default_factory=dict)


@dataclass
class PolicyConfig:
    """Parsed policy configuration."""

    policies: list[ParsedPolicy] = field(default_factory=list)
    observability: dict = field(default_factory=dict)
    logging: dict = field(default_factory=dict)
    source_path: Optional[str] = None


def parse_policy_config(raw: dict) -> PolicyConfig:
    policies = []
    for entry in raw.get("policies", []) or []:
        if "name" not in entry or "type" not in entry:
            raise PolicyConfigError(f"Policy entry missing 'name' or 'type': {entry}")
        if entry["type"] not in _HOOK_TYPES:
            valid = ", ".join(sorted(_HOOK_TYPES))
            raise PolicyConfigError(f"Unknown policy type '{entry['type']}'. Valid types: {valid}")
        policies.append(ParsedPolicy(name=entry["name"], type=entry["type"], config=entry.get("config", {}) or {}))
    return PolicyConfig(
        policies=policies,
        observability=raw.get("observability", {}) or {},
        logging=raw.get("logging", {}) or {},
    )


def load_config_file(path: str | Path = DEFAULT_CONFIG_PATH) -> PolicyConfig:
    """Load and parse a policy config file (does not build the engine)."""
    p = Path(path)
    if not p.is_file():
        raise PolicyConfigError(f"Policy config not found: {p}")
    raw = yaml.safe_load(p.read_text()) or {}
    if not isinstance(raw, dict):
        raise PolicyConfigError(f"Policy config must be a mapping, got {type(raw).__name__}")
    cfg = parse_policy_config(raw)
    cfg.source_path = str(p)
    return cfg


def build_engine(cfg: PolicyConfig, *, store: Optional[PolicyStore] = None) -> PolicyEngine:
    """Build a :class:`PolicyEngine` from a parsed config."""
    engine = PolicyEngine()
    shared_store = store
    for parsed in cfg.policies:
        hook_cls, needs_store = _HOOK_TYPES[parsed.type]
        kwargs = dict(parsed.config)
        if needs_store:
            if shared_store is None:
                DEFAULT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
                shared_store = PolicyStore(DEFAULT_STORE_PATH)
                shared_store.initialize()
            kwargs["store"] = shared_store
        try:
            engine.register(hook_cls(**kwargs))
        except TypeError as exc:
            raise PolicyConfigError(f"Invalid config for policy '{parsed.name}' ({parsed.type}): {exc}") from exc
    return engine


def load_policy_config(path: str | Path = DEFAULT_CONFIG_PATH, *, store: Optional[PolicyStore] = None) -> PolicyEngine:
    """Load a policy config file and return a ready :class:`PolicyEngine`."""
    return build_engine(load_config_file(path), store=store)


def make_audit_callback(audit_logger):
    """Adapt an HMAC ``AuditLogger`` into the engine's async audit callback."""

    async def _callback(entry: dict) -> None:
        audit_logger.log(
            action="policy_decision",
            actor=entry.get("actor", "unknown"),
            resource=entry.get("skill", ""),
            details={
                "phase": entry.get("phase"),
                "hook_name": entry.get("hook_name"),
                "decision": entry.get("decision"),
                "reason": entry.get("reason"),
                "namespace": entry.get("namespace"),
                "invocation_id": entry.get("invocation_id"),
                **(entry.get("details") or {}),
            },
        )

    return _callback
