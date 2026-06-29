"""Progressive deployment for SkillsOps (Milestone 3).

Canary / blue-green / staged rollouts with consistent-hash traffic routing,
health monitoring, and automatic rollback — every transition audited.
"""

from skillctl.deployment.engine import DeploymentEngine, DeploymentError
from skillctl.deployment.health import HealthMonitor
from skillctl.deployment.models import (
    BlueGreenConfig,
    CanaryConfig,
    Deployment,
    DeploymentState,
    DeploymentStrategy,
    HealthThreshold,
    StagedConfig,
)
from skillctl.deployment.router import TrafficRouter
from skillctl.deployment.store import DeploymentStore

__all__ = [
    "DeploymentEngine",
    "DeploymentError",
    "HealthMonitor",
    "TrafficRouter",
    "DeploymentStore",
    "Deployment",
    "DeploymentState",
    "DeploymentStrategy",
    "HealthThreshold",
    "CanaryConfig",
    "BlueGreenConfig",
    "StagedConfig",
]
