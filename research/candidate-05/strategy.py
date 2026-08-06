"""Candidate 05 active strategy entrypoint for early-participation ablation."""
from strategy_base import LiquidityResponseConfig
from strategy_v26_no_early_sponsored_ablation import NoEarlySponsoredParticipationStrategy as LiquidityResponseStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
