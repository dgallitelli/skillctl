"""Health monitoring for progressive deployments.

Computes error / denial / success rates and p99 latency from recorded
invocation metrics and compares them against thresholds. Synchronous and
deterministic — callers decide when to evaluate (no background loop), which
keeps behaviour testable.
"""

from __future__ import annotations

import time

from skillctl.deployment.models import Deployment, HealthThreshold
from skillctl.deployment.store import DeploymentStore


class HealthMonitor:
    def __init__(self, deployment_store: DeploymentStore):
        self._store = deployment_store

    def evaluate_health(self, deployment: Deployment, now: int | None = None) -> dict:
        threshold = HealthThreshold.from_dict(deployment.config.get("health_threshold"))
        now = now or int(time.time())
        start = now - threshold.evaluation_window_seconds
        rows = self._store.metrics_in_window(deployment.id, deployment.to_version, start, now)

        count = len(rows)
        if count < threshold.min_sample_size:
            return {
                "healthy": True,
                "reason": f"insufficient samples ({count} < {threshold.min_sample_size})",
                "metrics": {"invocation_count": count},
                "thresholds_breached": [],
            }

        errors = sum(r["error"] for r in rows)
        denials = sum(r["denied"] for r in rows)
        successes = sum(r["success"] for r in rows)
        latencies = sorted(r["latency_ms"] for r in rows)
        p99 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.99))]

        error_rate = errors / count
        denial_rate = denials / count
        success_rate = successes / count

        breached = []
        if error_rate > threshold.max_error_rate:
            breached.append("error_rate")
        if denial_rate > threshold.max_policy_denial_rate:
            breached.append("policy_denial_rate")
        if p99 > threshold.max_p99_latency_ms:
            breached.append("p99_latency_ms")
        if success_rate < threshold.min_success_rate:
            breached.append("success_rate")

        return {
            "healthy": not breached,
            "reason": "healthy" if not breached else f"thresholds breached: {', '.join(breached)}",
            "metrics": {
                "error_rate": round(error_rate, 4),
                "policy_denial_rate": round(denial_rate, 4),
                "p99_latency_ms": p99,
                "success_rate": round(success_rate, 4),
                "invocation_count": count,
            },
            "thresholds_breached": breached,
        }
