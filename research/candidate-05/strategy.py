"""Candidate 05 active strategy entrypoint for the v27 core-variable ablation."""
from strategy_base import LiquidityResponseConfig
from strategy_v27_ablation import NoDelayedResponseAblationStrategy as LiquidityResponseStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
