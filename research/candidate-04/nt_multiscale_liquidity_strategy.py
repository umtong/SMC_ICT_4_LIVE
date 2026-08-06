#!/usr/bin/env python3
"""Candidate-04 v15: multiscale liquidity-pool experiments.

V14's confirmation and target hierarchy are frozen. This module changes only
the structural pool which is swept before low-impact acceptance:

* INTERNAL_15M: a standard intraday internal-liquidity horizon. Because these
  pools are closer to current price, the premium/discount excursion threshold
  is 1.25 weighted dispersions.
* EXTERNAL_60M: a one-hour external-liquidity horizon with the original 1.50
  dispersion threshold.

Both remain conditioned on a rotational four-hour parent auction and use the
same external-first / dealing-range-fallback target hierarchy. These are two
market-structure hypotheses, not a parameter grid. NautilusTrader owns all
orders, fills, fees, positions, margin and NAV.
"""
from __future__ import annotations

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_low_impact_hybrid_target_strategy import LowImpactHybridTargetStrategy


class Internal15StrictStrategy(LowImpactHybridTargetStrategy):
    LIQUIDITY_WINDOW = 15
    BAND_SIGMA = 1.25
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.0
    DIRECT_MAX_BODY_ATR = 1.0


class Internal15NearEqualStrategy(LowImpactHybridTargetStrategy):
    LIQUIDITY_WINDOW = 15
    BAND_SIGMA = 1.25
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.10
    DIRECT_MAX_BODY_ATR = 1.0


class External60StrictStrategy(LowImpactHybridTargetStrategy):
    LIQUIDITY_WINDOW = 60
    BAND_SIGMA = 1.50
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.0
    DIRECT_MAX_BODY_ATR = 1.0


class External60NearEqualStrategy(LowImpactHybridTargetStrategy):
    LIQUIDITY_WINDOW = 60
    BAND_SIGMA = 1.50
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.10
    DIRECT_MAX_BODY_ATR = 1.0


__all__ = [
    "External60NearEqualStrategy",
    "External60StrictStrategy",
    "Internal15NearEqualStrategy",
    "Internal15StrictStrategy",
    "LiquidityTransitionConfig",
]
