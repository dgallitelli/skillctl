"""Experimental local deployment state model for SkillsOps.

Canary / blue-green / staged rollouts with consistent-hash traffic routing,
health evaluation, and rollback are library primitives. They do not control
registry or agent-runtime traffic without an embedding application.
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
