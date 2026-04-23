"""Data models for the skill optimizer.

All types support JSON round-trip via to_dict() / from_dict().
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class OptimizeConfig:
    """Configuration for an optimization run."""

    skill_path: str
    num_variants: int = 3
    threshold: float = 0.05
    max_iterations: int = 50
    plateau_limit: int = 3
    budget_usd: float = 10.0
    model: str = "us.anthropic.claude-opus-4-6-v1"
    aws_region: str = "us-east-1"
    approve: bool = False
    dry_run: bool = False
    timeout: int = 120
    agent: str = "claude"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> OptimizeConfig:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class LLMResponse:
    """Raw response from an LLM call with usage stats."""

    content: str
    input_tokens: int
    output_tokens: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LLMResponse:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TokenUsage:
    """Token counts and cost for a single LLM interaction."""

    input_tokens: int
    output_tokens: int
    cost_usd: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TokenUsage:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})



@dataclass
class BudgetState:
    """Cumulative budget tracking state."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    budget_usd: float = 10.0

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.budget_usd - self.total_cost_usd)

    @property
    def exhausted(self) -> bool:
        return self.total_cost_usd >= self.budget_usd

    def to_dict(self) -> dict:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": self.total_cost_usd,
            "budget_usd": self.budget_usd,
            "remaining_usd": self.remaining_usd,
            "exhausted": self.exhausted,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BudgetState:
        return cls(
            total_input_tokens=data["total_input_tokens"],
            total_output_tokens=data["total_output_tokens"],
            total_cost_usd=data["total_cost_usd"],
            budget_usd=data["budget_usd"],
        )


@dataclass
class EvalResult:
    """Structured result from a skill evaluation."""

    overall_score: Optional[float]  # 0.0-1.0, None on failure
    overall_grade: str
    passed: bool
    audit_score: Optional[float] = None
    functional_score: Optional[float] = None
    trigger_score: Optional[float] = None
    sections: dict = field(default_factory=dict)
    report_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> EvalResult:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Weakness:
    """A single identified weakness from failure analysis."""

    category: str  # "audit" | "functional" | "trigger"
    description: str
    severity: str  # "high" | "medium" | "low"
    evidence: list[str] = field(default_factory=list)
    hypothesis: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Weakness:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class FailureAnalysis:
    """LLM-produced analysis of eval failures."""

    weaknesses: list[Weakness]
    overall_summary: str
    tokens_used: TokenUsage

    def to_dict(self) -> dict:
        return {
            "weaknesses": [w.to_dict() for w in self.weaknesses],
            "overall_summary": self.overall_summary,
            "tokens_used": self.tokens_used.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> FailureAnalysis:
        return cls(
            weaknesses=[Weakness.from_dict(w) for w in data["weaknesses"]],
            overall_summary=data["overall_summary"],
            tokens_used=TokenUsage.from_dict(data["tokens_used"]),
        )


@dataclass
class Variant:
    """A candidate skill rewrite targeting a specific weakness."""

    id: str  # content hash
    content: str
    hypothesis: str
    target_weakness: str
    parent_id: str
    tokens_used: TokenUsage

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "hypothesis": self.hypothesis,
            "target_weakness": self.target_weakness,
            "parent_id": self.parent_id,
            "tokens_used": self.tokens_used.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Variant:
        return cls(
            id=data["id"],
            content=data["content"],
            hypothesis=data["hypothesis"],
            target_weakness=data["target_weakness"],
            parent_id=data["parent_id"],
            tokens_used=TokenUsage.from_dict(data["tokens_used"]),
        )


@dataclass
class PromotionDecision:
    """Result of the promotion gate check."""

    promoted: bool
    variant_id: Optional[str]
    current_score: float
    best_score: float
    delta: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PromotionDecision:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})



@dataclass
class VariantRecord:
    """Summary of a variant within a cycle record."""

    variant_id: str
    hypothesis: str
    target_weakness: str
    score: Optional[float]
    content_path: str
    eval_result_path: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> VariantRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class CycleRecord:
    """Record of a single optimization cycle."""

    cycle_number: int
    current_score: float
    eval_result_path: str
    failure_analysis_path: str
    variants: list[VariantRecord]
    promotion: Optional[PromotionDecision]
    cycle_cost_usd: float

    def to_dict(self) -> dict:
        return {
            "cycle_number": self.cycle_number,
            "current_score": self.current_score,
            "eval_result_path": self.eval_result_path,
            "failure_analysis_path": self.failure_analysis_path,
            "variants": [v.to_dict() for v in self.variants],
            "promotion": self.promotion.to_dict() if self.promotion else None,
            "cycle_cost_usd": self.cycle_cost_usd,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CycleRecord:
        return cls(
            cycle_number=data["cycle_number"],
            current_score=data["current_score"],
            eval_result_path=data["eval_result_path"],
            failure_analysis_path=data["failure_analysis_path"],
            variants=[VariantRecord.from_dict(v) for v in data["variants"]],
            promotion=PromotionDecision.from_dict(data["promotion"]) if data.get("promotion") else None,
            cycle_cost_usd=data["cycle_cost_usd"],
        )


@dataclass
class OptimizationRun:
    """Full manifest for an optimization run."""

    run_id: str
    skill_name: str
    skill_path: str
    original_content_hash: str
    config: dict
    started_at: str
    finished_at: Optional[str]
    status: str
    cycles: list[CycleRecord]
    final_score: Optional[float]
    initial_score: Optional[float]
    total_cost_usd: float
    promoted_variant_id: Optional[str]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "skill_name": self.skill_name,
            "skill_path": self.skill_path,
            "original_content_hash": self.original_content_hash,
            "config": self.config,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "cycles": [c.to_dict() for c in self.cycles],
            "final_score": self.final_score,
            "initial_score": self.initial_score,
            "total_cost_usd": self.total_cost_usd,
            "promoted_variant_id": self.promoted_variant_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> OptimizationRun:
        return cls(
            run_id=data["run_id"],
            skill_name=data["skill_name"],
            skill_path=data["skill_path"],
            original_content_hash=data["original_content_hash"],
            config=data["config"],
            started_at=data["started_at"],
            finished_at=data.get("finished_at"),
            status=data["status"],
            cycles=[CycleRecord.from_dict(c) for c in data["cycles"]],
            final_score=data.get("final_score"),
            initial_score=data.get("initial_score"),
            total_cost_usd=data["total_cost_usd"],
            promoted_variant_id=data.get("promoted_variant_id"),
        )
