"""Candidate 05 active strategy entrypoint."""
from strategy_base import LiquidityResponseConfig
from strategy_v19 import BlindRetraceAblationStrategy as LiquidityResponseStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
