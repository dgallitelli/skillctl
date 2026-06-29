"""Traffic router for progressive deployments.

Resolves which skill version to serve for a given invocation based on the
active deployment's traffic split. Consistent-hash routing keeps a given actor
on the same version for the duration of a deployment.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from skillctl.deployment.models import DeploymentState, DeploymentStrategy
from skillctl.deployment.store import DeploymentStore


class TrafficRouter:
    def __init__(self, deployment_store: DeploymentStore):
        self._store = deployment_store

    def resolve_version(
        self, skill_name: str, skill_namespace: str, actor_id: str, current_version: Optional[str] = None
    ) -> str:
        deployment = self._store.active_for_skill(skill_name)
        if deployment is None:
            return current_version or ""

        # Rolling back / rolled back → serve the previous version.
        if deployment.state in (DeploymentState.ROLLING_BACK, DeploymentState.ROLLED_BACK, DeploymentState.FAILED):
            return deployment.from_version or current_version or deployment.to_version

        # Fully promoted → the new version is live for everyone.
        if deployment.state == DeploymentState.COMPLETED:
            if deployment.strategy == DeploymentStrategy.BLUE_GREEN:
                active = self._store.get_bluegreen_active(deployment.id)
                return (
                    deployment.to_version if active == "green" else (deployment.from_version or deployment.to_version)
                )
            return deployment.to_version

        if deployment.strategy == DeploymentStrategy.IMMEDIATE:
            return deployment.to_version

        if deployment.strategy == DeploymentStrategy.BLUE_GREEN:
            active = self._store.get_bluegreen_active(deployment.id)
            return deployment.to_version if active == "green" else (deployment.from_version or deployment.to_version)

        # CANARY / STAGED in progress: traffic-percent split via consistent hash.
        if self._hash_route(actor_id, deployment.current_traffic_percent):
            return deployment.to_version
        return deployment.from_version or deployment.to_version

    @staticmethod
    def _hash_route(actor_id: str, traffic_percent: float) -> bool:
        if traffic_percent >= 1.0:
            return True
        if traffic_percent <= 0.0:
            return False
        hash_bytes = hashlib.sha256(actor_id.encode()).digest()[:4]
        hash_int = int.from_bytes(hash_bytes, "big")
        threshold = int(traffic_percent * 0xFFFFFFFF)
        return hash_int < threshold
