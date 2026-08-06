#!/usr/bin/env python3
"""Candidate 05 production import surface: sponsored CHoCH fallback v15."""
from strategy_base import LiquidityResponseConfig
from strategy_v15 import SponsoredChochFallbackStrategy as LiquidityResponseStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
