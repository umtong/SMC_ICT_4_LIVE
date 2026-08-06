"""Candidate 05 active strategy entrypoint."""
from strategy_base import LiquidityResponseConfig
from strategy_v21 import FrozenScenarioTargetStrategy as LiquidityResponseStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
