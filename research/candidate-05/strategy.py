#!/usr/bin/env python3
"""Candidate 05 import surface: preserve v1-v3 and execute the v4 state machine."""
from strategy_base import LiquidityResponseConfig
from strategy_v4 import LiquidityResponseDepthStrategy as LiquidityResponseStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
