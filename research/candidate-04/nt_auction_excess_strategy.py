#!/usr/bin/env python3
"""Candidate-04 v8: rolling auction-excess liquidity reversion.

The scenario is an auction-state implementation rather than a candle pattern:

1. The prior four-hour auction must be rotational, measured by low net/path
   efficiency.
2. A completed bar must take a prior 30-minute liquidity extreme while reaching
   a statistically unusual premium/discount relative to a past-only
   volume-weighted fair value.
3. The same bar must close back through the swept level with directional close
   location and above-normal participation.
4. The causal target is the pre-event VWAP; invalidation is the sweep extreme.

All order matching, fills, fees, positions, margin, liquidation and NAV remain
inside NautilusTrader.
"""
from __future__ import annotations

import math
from typing import Iterable

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup


SCENARIO = "ROLLING_AUCTION_EXCESS_REVERSION"


def weighted_location(
    prices: Iterable[float],
    weights: Iterable[float],
) -> tuple[float, float]:
    """Return weighted fair value and dispersion for completed past bars."""

    pairs = [
        (float(price), max(float(weight), 0.0))
        for price, weight in zip(prices, weights)
        if math.isfinite(float(price)) and math.isfinite(float(weight))
    ]
    total = sum(weight for _, weight in pairs)
    if not pairs or total <= 0.0:
        return float("nan"), float("nan")
    mean = sum(price * weight for price, weight in pairs) / total
    variance = sum(weight * (price - mean) ** 2 for price, weight in pairs) / total
    return mean, math.sqrt(max(variance, 0.0))


class AuctionExcessReversionStrategy(LiquidityTransitionStrategy):
    VALUE_WINDOW = 240
    LIQUIDITY_WINDOW = 30
    BAND_SIGMA = 1.50
    MAX_EFFICIENCY_240 = 0.32
    MIN_VOLUME_BURST = 1.20
    MIN_CLOSE_LOCATION = 0.60
    MIN_RECLAIM_ATR = 0.05
    TARGET_NET_R = 1.50

    def _detect_session_sweep(self, row: dict[str, float | int]) -> bool:
        rows = list(self.bars)
        if len(rows) < self.VALUE_WINDOW + 2:
            return False
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return False
        if self._efficiency(self.VALUE_WINDOW) > self.MAX_EFFICIENCY_240:
            return False

        history = rows[-(self.VALUE_WINDOW + 1) : -1]
        typical = [
            (float(item["high"]) + float(item["low"]) + float(item["close"])) / 3.0
            for item in history
        ]
        volume = [float(item["volume"]) for item in history]
        fair_value, dispersion = weighted_location(typical, volume)
        if not math.isfinite(fair_value) or not math.isfinite(dispersion) or dispersion <= 0.0:
            return False

        pool = rows[-(self.LIQUIDITY_WINDOW + 1) : -1]
        prior_high = max(float(item["high"]) for item in pool)
        prior_low = min(float(item["low"]) for item in pool)
        lower_band = fair_value - self.BAND_SIGMA * dispersion
        upper_band = fair_value + self.BAND_SIGMA * dispersion

        low_swept = (
            float(row["low"]) < prior_low
            and float(row["low"]) <= lower_band
            and float(row["close"]) > prior_low
        )
        high_swept = (
            float(row["high"]) > prior_high
            and float(row["high"]) >= upper_band
            and float(row["close"]) < prior_high
        )
        if low_swept == high_swept:
            return False

        side = 1 if low_swept else -1
        close_location = self._close_location(row, side)
        volume_burst = self._volume_burst()
        reclaim = (
            (float(row["close"]) - prior_low) / atr
            if side > 0
            else (prior_high - float(row["close"])) / atr
        )
        if not (
            close_location >= self.MIN_CLOSE_LOCATION
            and volume_burst >= self.MIN_VOLUME_BURST
            and reclaim >= self.MIN_RECLAIM_ATR
        ):
            return False

        last_entry = self.last_entry_by_scenario.get(SCENARIO, -10**12)
        if self.bar_index - last_entry < self.config.cooldown_bars:
            return False

        setup = PendingSetup(
            scenario=SCENARIO,
            side=side,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            extreme=float(row["low"] if side > 0 else row["high"]),
            structure=prior_low if side > 0 else prior_high,
            atr=atr,
            target_reference=fair_value,
            details={
                "fair_value": fair_value,
                "dispersion": dispersion,
                "lower_band": lower_band,
                "upper_band": upper_band,
                "prior_high": prior_high,
                "prior_low": prior_low,
                "auction_efficiency_240": self._efficiency(self.VALUE_WINDOW),
                "volume_burst": volume_burst,
                "close_location": close_location,
                "reclaim_atr": reclaim,
                "premium_discount_sigma": (
                    (float(row["low"]) - fair_value) / dispersion
                    if side > 0
                    else (float(row["high"]) - fair_value) / dispersion
                ),
            },
        )
        self._event("AUCTION_EXCESS_CONFIRMED", SCENARIO, row, setup.details)
        submitted = LiquidityTransitionStrategy._submit_bracket(
            self,
            setup,
            row,
            self.TARGET_NET_R,
            setup.details,
        )
        if not submitted:
            self._event("AUCTION_EXCESS_EXECUTION_REJECTED", SCENARIO, row, setup.details)
        return True

    def _detect_trend_sweep(self, row: dict[str, float | int]) -> bool:
        return False


__all__ = [
    "AuctionExcessReversionStrategy",
    "LiquidityTransitionConfig",
    "weighted_location",
]
