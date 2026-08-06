"""Candidate 05 active strategy entrypoint."""
from strategy_base import LiquidityResponseConfig
from strategy_v23 import PositionBuildingAcceptanceAblationStrategy as LiquidityResponseStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
