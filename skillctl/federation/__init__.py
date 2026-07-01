"""Multi-registry federation for SkillsOps (Milestone 4)."""

from skillctl.federation.promote import FederationError, PromotionResult, promote_skill

__all__ = ["promote_skill", "PromotionResult", "FederationError"]
