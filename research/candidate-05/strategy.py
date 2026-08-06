#!/usr/bin/env python3
"""Candidate 05 import surface: execute the v8 tail-flow liquidity candidate."""
from strategy_base import LiquidityResponseConfig
from strategy_v8 import TailFlowLiquidityStrategy as LiquidityResponseStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
