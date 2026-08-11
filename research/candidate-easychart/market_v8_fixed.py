"""Corrected repeated-wick reference lifecycle for v8.

A farther wick extends the same visible liquidity shelf when the candle body
still respects the shelf's inner boundary.  The reference is replaced only
when the body accepts through that boundary.  This is the intended
PyIndicators-style body/wick distinction and fixes the initial transcription
of the adaptation without changing the trading scenario.
"""
from __future__ import annotations

from domain_v3 import Candle, Side
from market_v8 import (
    EasyChartLiquidityPoolEngine,
    PoolDetectorUpdate,
    PoolTrapConfig,
    WickLiquidityPoolDetector,
)


class CorrectedWickLiquidityPoolDetector(WickLiquidityPoolDetector):
    def on_candle(self, candle: Candle, index: int) -> PoolDetectorUpdate:
        body_top, body_bottom = self._body(candle)
        mitigated = self._mitigations(candle)

        if self.high_ref is None:
            self._new_high_ref(candle, index, body_top)
        if self.low_ref is None:
            self._new_low_ref(candle, index, body_bottom)
        assert self.high_ref is not None and self.low_ref is not None

        high_ref = self.high_ref
        # A higher wick with a body still below/equal to the stable body
        # boundary is another rejection/contact, not a new reference.
        if candle.high > high_ref.outer and body_top > high_ref.inner:
            self._new_high_ref(candle, index, body_top)
            high_ref = self.high_ref

        low_ref = self.low_ref
        # Symmetrically, a lower wick whose body remains above/equal to the
        # boundary extends the existing support-side pool.
        if candle.low < low_ref.outer and body_bottom < low_ref.inner:
            self._new_low_ref(candle, index, body_bottom)
            low_ref = self.low_ref
        assert high_ref is not None and low_ref is not None

        high_contact = candle.high > high_ref.inner and body_top <= high_ref.inner
        if high_contact and index != high_ref.origin_index:
            high_ref.outer = max(high_ref.outer, candle.high)
            if index - high_ref.last_contact_index >= self.config.gap_bars:
                high_ref.contacts += 1
                high_ref.last_contact_index = index
                self._count("high_wick_contacts")

        low_contact = candle.low < low_ref.inner and body_bottom >= low_ref.inner
        if low_contact and index != low_ref.origin_index:
            low_ref.outer = min(low_ref.outer, candle.low)
            if index - low_ref.last_contact_index >= self.config.gap_bars:
                low_ref.contacts += 1
                low_ref.last_contact_index = index
                self._count("low_wick_contacts")

        formed = []
        if (
            not high_ref.emitted
            and high_ref.contacts >= self.config.contact_count
            and index - high_ref.last_contact_index >= self.config.confirmation_bars
            and candle.close < high_ref.inner
        ):
            high_ref.emitted = True
            pool = self._formed_pool(high_ref, candle)
            if self._activate(pool):
                formed.append(pool)

        if (
            not low_ref.emitted
            and low_ref.contacts >= self.config.contact_count
            and index - low_ref.last_contact_index >= self.config.confirmation_bars
            and candle.close > low_ref.inner
        ):
            low_ref.emitted = True
            pool = self._formed_pool(low_ref, candle)
            if self._activate(pool):
                formed.append(pool)

        return PoolDetectorUpdate(tuple(formed), tuple(mitigated))


class CorrectedEasyChartLiquidityPoolEngine(EasyChartLiquidityPoolEngine):
    def __init__(self, symbol: str, config: PoolTrapConfig) -> None:
        super().__init__(symbol, config)
        self.detector = CorrectedWickLiquidityPoolDetector(symbol, config.detector)


__all__ = [
    "CorrectedEasyChartLiquidityPoolEngine",
    "CorrectedWickLiquidityPoolDetector",
]
