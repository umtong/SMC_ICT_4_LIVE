#!/usr/bin/env python3
"""Candidate-04 v16: nested auction-scale liquidity acceptance.

The 15/30/60-minute pools mostly described the same parent events. V16 avoids
counting those duplicates and tests two genuinely different nested auctions:

* MICRO_5_IN_60: five-minute internal liquidity inside a rotational one-hour
  parent auction. This targets short inventory imbalances and requires a one-
  dispersion excursion from the parent fair value.
* INTERNAL_10_IN_120: ten-minute liquidity inside a rotational two-hour parent
  auction. This targets intermediate stop pools with a 1.15-dispersion
  excursion.

The profitable low-impact acceptance and causal target hierarchy from V14 are
unchanged. NautilusTrader owns all orders, fills, fees, positions, margin,
liquidation and NAV.
"""
from __future__ import annotations

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_low_impact_hybrid_target_strategy import LowImpactHybridTargetStrategy


class Micro5In60StrictStrategy(LowImpactHybridTargetStrategy):
    VALUE_WINDOW = 60
    LIQUIDITY_WINDOW = 5
    BAND_SIGMA = 1.00
    MAX_EFFICIENCY_240 = 0.28
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.0
    DIRECT_MAX_BODY_ATR = 1.0


class Micro5In60NearEqualStrategy(LowImpactHybridTargetStrategy):
    VALUE_WINDOW = 60
    LIQUIDITY_WINDOW = 5
    BAND_SIGMA = 1.00
    MAX_EFFICIENCY_240 = 0.28
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.10
    DIRECT_MAX_BODY_ATR = 1.0


class Internal10In120StrictStrategy(LowImpactHybridTargetStrategy):
    VALUE_WINDOW = 120
    LIQUIDITY_WINDOW = 10
    BAND_SIGMA = 1.15
    MAX_EFFICIENCY_240 = 0.30
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.0
    DIRECT_MAX_BODY_ATR = 1.0


class Internal10In120NearEqualStrategy(LowImpactHybridTargetStrategy):
    VALUE_WINDOW = 120
    LIQUIDITY_WINDOW = 10
    BAND_SIGMA = 1.15
    MAX_EFFICIENCY_240 = 0.30
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.10
    DIRECT_MAX_BODY_ATR = 1.0


__all__ = [
    "Internal10In120NearEqualStrategy",
    "Internal10In120StrictStrategy",
    "LiquidityTransitionConfig",
    "Micro5In60NearEqualStrategy",
    "Micro5In60StrictStrategy",
]
