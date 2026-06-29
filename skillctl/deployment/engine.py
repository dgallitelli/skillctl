"""Deployment engine orchestrating progressive rollouts.

Synchronous lifecycle management (create / promote / rollback / pause / resume)
plus an explicit health evaluation that can trigger auto-rollback. Keeping
health checks caller-driven (rather than a background loop) makes the behaviour
deterministic and testable. Every state transition is recorded in the audit
chain when an audit logger is supplied.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from skillctl.deployment.health import HealthMonitor
from skillctl.deployment.models import Deployment, DeploymentState, DeploymentStrategy
from skillctl.deployment.store import DeploymentStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentError(Exception):
    pass


class DeploymentEngine:
    def __init__(self, store: DeploymentStore, audit_logger=None, rbac_check=None):
        """rbac_check: optional callable(action: str, namespace: str) -> bool."""
        self.store = store
        self.audit_logger = audit_logger
        self.rbac_check = rbac_check

    def _audit(self, action: str, deployment: Deployment, **details) -> None:
        if self.audit_logger is None:
            return
        self.audit_logger.log(
            action=action,
            actor=details.pop("actor", deployment.initiated_by) or "system",
            resource=f"{deployment.skill_name}@{deployment.to_version}",
            details={"deployment_id": deployment.id, "namespace": deployment.skill_namespace, **details},
        )

    def create(
        self,
        *,
        skill_name: str,
        skill_namespace: str,
        to_version: str,
        strategy: DeploymentStrategy,
        config: Optional[dict] = None,
        from_version: Optional[str] = None,
        initiated_by: str = "",
    ) -> Deployment:
        if self.rbac_check and not self.rbac_check("skill:publish", skill_namespace):
            raise DeploymentError(f"'{initiated_by}' lacks skill:publish in '{skill_namespace}'")

        config = config or {}
        dep = Deployment(
            id=f"dep-{uuid.uuid4().hex[:8]}",
            skill_name=skill_name,
            skill_namespace=skill_namespace,
            from_version=from_version,
            to_version=to_version,
            strategy=strategy,
            state=DeploymentState.IN_PROGRESS,
            config=config,
            started_at=_now(),
            initiated_by=initiated_by,
        )

        if strategy == DeploymentStrategy.IMMEDIATE:
            dep.current_traffic_percent = 1.0
            dep.state = DeploymentState.COMPLETED
            dep.completed_at = _now()
        elif strategy == DeploymentStrategy.CANARY:
            stages = config.get("stages") or [0.01, 0.05, 0.25, 0.50, 1.0]
            dep.current_stage = 0
            dep.current_traffic_percent = stages[0]
        elif strategy == DeploymentStrategy.BLUE_GREEN:
            dep.current_traffic_percent = 0.0  # green not yet serving
        elif strategy == DeploymentStrategy.STAGED:
            stages = config.get("stages") or []
            dep.current_stage = 0
            dep.current_traffic_percent = stages[0].get("traffic_percent", 0.0) if stages else 0.0

        active = "blue" if strategy == DeploymentStrategy.BLUE_GREEN else "blue"
        self.store.save(dep, bluegreen_active=active)
        self._audit(
            "deployment.created", dep, strategy=strategy.value, to_version=to_version, from_version=from_version
        )
        return dep

    def _require(self, deployment_id: str) -> Deployment:
        dep = self.store.get(deployment_id)
        if dep is None:
            raise DeploymentError(f"Deployment '{deployment_id}' not found")
        return dep

    def promote(self, deployment_id: str, approved_by: str = "") -> Deployment:
        dep = self._require(deployment_id)
        if dep.state not in (DeploymentState.IN_PROGRESS, DeploymentState.PAUSED, DeploymentState.PROMOTING):
            raise DeploymentError(f"Cannot promote a deployment in state {dep.state.value}")

        if self.rbac_check and not self.rbac_check("skill:publish", dep.skill_namespace):
            raise DeploymentError(f"'{approved_by}' lacks permission to promote in '{dep.skill_namespace}'")
        if approved_by:
            dep.approved_by = dep.approved_by + [approved_by]

        active = self.store.get_bluegreen_active(dep.id)
        if dep.strategy == DeploymentStrategy.BLUE_GREEN:
            active = "green"  # switch traffic to the new version
            dep.current_traffic_percent = 1.0
            dep.state = DeploymentState.COMPLETED
            dep.completed_at = _now()
        elif dep.strategy == DeploymentStrategy.CANARY:
            stages = dep.config.get("stages") or [0.01, 0.05, 0.25, 0.50, 1.0]
            dep.current_stage = min(dep.current_stage + 1, len(stages) - 1)
            dep.current_traffic_percent = stages[dep.current_stage]
            if dep.current_traffic_percent >= 1.0:
                dep.state = DeploymentState.COMPLETED
                dep.completed_at = _now()
        elif dep.strategy == DeploymentStrategy.STAGED:
            stages = dep.config.get("stages") or []
            dep.current_stage = min(dep.current_stage + 1, max(len(stages) - 1, 0))
            if stages:
                dep.current_traffic_percent = stages[dep.current_stage].get("traffic_percent", 1.0)
            if dep.current_stage >= len(stages) - 1:
                dep.state = DeploymentState.COMPLETED
                dep.completed_at = _now()
        else:
            dep.state = DeploymentState.COMPLETED
            dep.completed_at = _now()

        self.store.save(dep, bluegreen_active=active)
        self._audit(
            "deployment.promoted",
            dep,
            actor=approved_by,
            stage=dep.current_stage,
            traffic=dep.current_traffic_percent,
            state=dep.state.value,
        )
        return dep

    def rollback(self, deployment_id: str, reason: str = "", rolled_back_by: str = "") -> Deployment:
        dep = self._require(deployment_id)
        dep.state = DeploymentState.ROLLED_BACK
        dep.current_traffic_percent = 0.0
        dep.rolled_back_by = rolled_back_by or "system"
        dep.rollback_reason = reason
        dep.completed_at = _now()
        self.store.save(dep, bluegreen_active="blue")
        self._audit("deployment.rolled_back", dep, actor=rolled_back_by, reason=reason)
        return dep

    def pause(self, deployment_id: str) -> Deployment:
        dep = self._require(deployment_id)
        dep.state = DeploymentState.PAUSED
        self.store.save(dep)
        self._audit("deployment.paused", dep)
        return dep

    def resume(self, deployment_id: str) -> Deployment:
        dep = self._require(deployment_id)
        dep.state = DeploymentState.IN_PROGRESS
        self.store.save(dep)
        self._audit("deployment.resumed", dep)
        return dep

    def check_health(self, deployment_id: str, monitor: HealthMonitor, now: int | None = None) -> dict:
        return monitor.evaluate_health(self._require(deployment_id), now=now)

    def evaluate_and_maybe_rollback(self, deployment_id: str, monitor: HealthMonitor, now: int | None = None) -> dict:
        """Evaluate health; auto-rollback if unhealthy and auto_rollback is set."""
        dep = self._require(deployment_id)
        health = monitor.evaluate_health(dep, now=now)
        if not health["healthy"] and dep.config.get("auto_rollback", False):
            self.rollback(deployment_id, reason=health["reason"], rolled_back_by="auto-rollback")
            health["rolled_back"] = True
        else:
            health["rolled_back"] = False
        return health
