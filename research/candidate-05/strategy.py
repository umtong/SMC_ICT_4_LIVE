#!/usr/bin/env python3
"""Candidate 05 import surface: execute the v10 protected liquidity path."""
from strategy_base import LiquidityResponseConfig
from strategy_v10 import ProtectedLiquidityPathStrategy as LiquidityResponseStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
