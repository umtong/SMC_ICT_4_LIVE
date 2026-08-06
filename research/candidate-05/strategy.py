#!/usr/bin/env python3
"""Candidate 05 import surface: execute v5 while preserving prior variants."""
from strategy_base import LiquidityResponseConfig
from strategy_v5 import LiquidityResponseBreakawayStrategy as LiquidityResponseStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
