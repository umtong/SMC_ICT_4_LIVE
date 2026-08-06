#!/usr/bin/env python3
"""Candidate 05 import surface: preserve v1/v2 and execute the v3 state machine."""
from strategy_base import LiquidityResponseConfig
from strategy_v3 import LiquidityResponseExternalRetraceStrategy as LiquidityResponseStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
