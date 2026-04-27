"""Centralized configuration for skillctl.

Reads/writes ``~/.skillctl/config.yaml`` with a typed schema.
All config access should go through this module — not raw YAML loads.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import yaml

CONFIG_DIR = Path.home() / ".skillctl"
CONFIG_PATH = CONFIG_DIR / "config.yaml"


@dataclass
class RegistryLocalConfig:
    url: Optional[str] = None
    token: Optional[str] = None


@dataclass
class RegistryAgentRegistryConfig:
    registry_id: Optional[str] = None
    region: str = "us-east-1"


@dataclass
class RegistryConfig:
    backend: str = "local"  # "local" | "agent-registry"
    local: RegistryLocalConfig = field(default_factory=RegistryLocalConfig)
    agent_registry: RegistryAgentRegistryConfig = field(default_factory=RegistryAgentRegistryConfig)


@dataclass
class OptimizeConfig:
    model: str = "bedrock/us.anthropic.claude-opus-4-6-v1"
    max_tokens: int = 4096
    budget_usd: float = 10.0


@dataclass
class GitHubConfig:
    token: Optional[str] = None
    client_id: Optional[str] = None


@dataclass
class SkillctlConfig:
    registry: RegistryConfig = field(default_factory=RegistryConfig)
    optimize: OptimizeConfig = field(default_factory=OptimizeConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)

    def to_dict(self) -> dict:
        """Serialize to a dict, omitting None values for cleaner YAML."""

        def _clean(d):
            if isinstance(d, dict):
                return {k: _clean(v) for k, v in d.items() if v is not None}
            return d

        return _clean(asdict(self))


def load_config() -> SkillctlConfig:
    """Load config from ~/.skillctl/config.yaml, returning defaults if missing or corrupt."""
    if not CONFIG_PATH.exists():
        return SkillctlConfig()

    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text())
    except yaml.YAMLError:
        return SkillctlConfig()

    if not isinstance(raw, dict):
        return SkillctlConfig()

    return _parse_config(raw)


def save_config(config: SkillctlConfig) -> None:
    """Write config to ~/.skillctl/config.yaml with restricted permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.dump(config.to_dict(), default_flow_style=False, sort_keys=False))
    CONFIG_PATH.chmod(0o600)


def _parse_config(raw: dict) -> SkillctlConfig:
    """Parse a raw dict into a typed SkillctlConfig."""
    reg_raw = raw.get("registry") or {}
    local_raw = reg_raw.get("local") or {}
    ar_raw = reg_raw.get("agent_registry") or {}
    opt_raw = raw.get("optimize") or {}
    gh_raw = raw.get("github") or {}

    # Backward compat: old config had registry.url and registry.token at top level
    if "url" in reg_raw and "local" not in reg_raw:
        local_raw = {"url": reg_raw["url"], "token": reg_raw.get("token")}

    return SkillctlConfig(
        registry=RegistryConfig(
            backend=reg_raw.get("backend", "local"),
            local=RegistryLocalConfig(
                url=local_raw.get("url"),
                token=local_raw.get("token"),
            ),
            agent_registry=RegistryAgentRegistryConfig(
                registry_id=ar_raw.get("registry_id"),
                region=ar_raw.get("region", "us-east-1"),
            ),
        ),
        optimize=OptimizeConfig(
            model=opt_raw.get("model", "bedrock/us.anthropic.claude-opus-4-6-v1"),
            max_tokens=opt_raw.get("max_tokens", 4096),
            budget_usd=opt_raw.get("budget_usd", 10.0),
        ),
        github=GitHubConfig(
            token=gh_raw.get("token"),
            client_id=gh_raw.get("client_id"),
        ),
    )


def run_configure_wizard(config: SkillctlConfig | None = None) -> SkillctlConfig:
    """Interactive configuration wizard. Returns the updated config."""
    if config is None:
        config = load_config()

    print("skillctl configure\n")

    # --- Registry ---
    backend = _prompt(
        "Registry backend [local/agent-registry]",
        config.registry.backend,
    )
    config.registry.backend = backend

    if backend == "agent-registry":
        config.registry.agent_registry.registry_id = (
            _prompt(
                "  Registry ID (ARN)",
                config.registry.agent_registry.registry_id or "",
            )
            or None
        )
        config.registry.agent_registry.region = _prompt(
            "  AWS region",
            config.registry.agent_registry.region,
        )
    else:
        config.registry.local.url = (
            _prompt(
                "  Registry URL",
                config.registry.local.url or "",
            )
            or None
        )
        token_val = _prompt(
            "  Auth token (leave blank if auth disabled)",
            "",
        )
        if token_val:
            config.registry.local.token = token_val

    # --- Optimizer ---
    print()
    config.optimize.model = _prompt(
        "Optimizer model",
        config.optimize.model,
    )
    budget_str = _prompt(
        "Optimizer budget in USD",
        str(config.optimize.budget_usd),
    )
    try:
        config.optimize.budget_usd = float(budget_str)
    except ValueError:
        pass

    return config


def _prompt(label: str, default: str) -> str:
    """Prompt for input with a default value shown in parens."""
    display = f" ({default})" if default else ""
    try:
        value = input(f"{label}{display}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return value if value else default
