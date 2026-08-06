"""Candidate 05 active strategy entrypoint."""
from strategy_base import LiquidityResponseConfig
from strategy_v25 import ConfirmedSecondTouchStrategy as LiquidityResponseStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
