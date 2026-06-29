"""Progressive deployment models: canary, blue-green, staged, immediate."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DeploymentStrategy(Enum):
    CANARY = "canary"
    BLUE_GREEN = "blue-green"
    STAGED = "staged"
    IMMEDIATE = "immediate"


class DeploymentState(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    PROMOTING = "promoting"
    ROLLING_BACK = "rolling_back"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass
class HealthThreshold:
    max_error_rate: float = 0.05
    max_policy_denial_rate: float = 0.10
    max_p99_latency_ms: float = 5000
    min_success_rate: float = 0.95
    evaluation_window_seconds: int = 300
    min_sample_size: int = 10

    def to_dict(self) -> dict:
        return {
            "max_error_rate": self.max_error_rate,
            "max_policy_denial_rate": self.max_policy_denial_rate,
            "max_p99_latency_ms": self.max_p99_latency_ms,
            "min_success_rate": self.min_success_rate,
            "evaluation_window_seconds": self.evaluation_window_seconds,
            "min_sample_size": self.min_sample_size,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "HealthThreshold":
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__annotations__})


@dataclass
class CanaryConfig:
    stages: list[float] = field(default_factory=lambda: [0.01, 0.05, 0.25, 0.50, 1.0])
    stage_duration_minutes: int = 30
    auto_promote: bool = True
    auto_rollback: bool = True
    routing_method: str = "user_hash"
    health_threshold: HealthThreshold = field(default_factory=HealthThreshold)


@dataclass
class BlueGreenConfig:
    warmup_duration_minutes: int = 10
    parallel_duration_minutes: int = 60
    auto_switch: bool = False
    keep_blue_minutes: int = 120
    health_threshold: HealthThreshold = field(default_factory=HealthThreshold)


@dataclass
class StagedConfig:
    stages: list[dict] = field(
        default_factory=lambda: [
            {"name": "development", "auto_promote": True, "approvers": []},
            {"name": "staging", "auto_promote": False, "approvers": ["publisher"]},
            {"name": "production", "auto_promote": False, "approvers": ["admin"]},
        ]
    )
    health_threshold: HealthThreshold = field(default_factory=HealthThreshold)


@dataclass
class Deployment:
    id: str
    skill_name: str
    skill_namespace: str
    from_version: Optional[str]
    to_version: str
    strategy: DeploymentStrategy
    state: DeploymentState
    config: dict = field(default_factory=dict)
    current_stage: int = 0
    current_traffic_percent: float = 0.0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    initiated_by: str = ""
    approved_by: list[str] = field(default_factory=list)
    rolled_back_by: Optional[str] = None
    rollback_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "skill_name": self.skill_name,
            "skill_namespace": self.skill_namespace,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "strategy": self.strategy.value,
            "state": self.state.value,
            "config": self.config,
            "current_stage": self.current_stage,
            "current_traffic_percent": self.current_traffic_percent,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "initiated_by": self.initiated_by,
            "approved_by": self.approved_by,
            "rolled_back_by": self.rolled_back_by,
            "rollback_reason": self.rollback_reason,
        }
