#!/usr/bin/env python3
"""Candidate-04 v9: auction-excess rejection-failure continuation.

V8 established a clear causal failure: 15 statistically extreme liquidity
rejections produced zero successful returns to fair value. V9 does not invert a
backtest label blindly. It treats each apparent rejection as a competing-risk
probe:

* fair value reached first -> the failed auction succeeded, no continuation;
* the sweep extreme is accepted first with directional participation -> the
  rejection failed and continuation becomes tradable.

The continuation stop is the reclaimed prior-liquidity boundary. Its causal
measured-move target is one pre-event dealing-range width beyond the accepted
extreme. Orders, fills, fees, margin and NAV are handled only by NautilusTrader.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from nt_auction_excess_strategy import weighted_location
from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup


SCENARIO = "AUCTION_EXCESS_REJECTION_FAILURE_CONTINUATION"


@dataclass(slots=True)
class ExcessProbe:
    created_index: int
    expires_index: int
    reversal_side: int
    continuation_side: int
    fair_value: float
    sweep_extreme: float
    reclaimed_boundary: float
    prior_high: float
    prior_low: float
    dispersion: float
    details: dict[str, float]


class AuctionExcessFailureContinuationStrategy(LiquidityTransitionStrategy):
    VALUE_WINDOW = 240
    LIQUIDITY_WINDOW = 30
    BAND_SIGMA = 1.50
    MAX_EFFICIENCY_240 = 0.32
    MIN_REJECTION_VOLUME_BURST = 1.20
    MIN_REJECTION_CLOSE_LOCATION = 0.60
    MIN_RECLAIM_ATR = 0.05
    FAILURE_WINDOW = 30
    FAILURE_BODY_ATR = 0.35
    FAILURE_VOLUME_BURST = 1.10
    FAILURE_CLOSE_LOCATION = 0.65
    TARGET_NET_R = 1.60

    def __init__(self, config: LiquidityTransitionConfig) -> None:
        super().__init__(config)
        self.excess_probe: ExcessProbe | None = None

    def on_bar(self, bar: object) -> None:
        super().on_bar(bar)
        if self.bars:
            self._advance_probe(self.bars[-1])

    def _detect_session_sweep(self, row: dict[str, float | int]) -> bool:
        if self.excess_probe is not None:
            return False
        rows = list(self.bars)
        if len(rows) < self.VALUE_WINDOW + 2:
            return False
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return False
        efficiency = self._efficiency(self.VALUE_WINDOW)
        if efficiency > self.MAX_EFFICIENCY_240:
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

        reversal_side = 1 if low_swept else -1
        close_location = self._close_location(row, reversal_side)
        volume_burst = self._volume_burst()
        reclaim = (
            (float(row["close"]) - prior_low) / atr
            if reversal_side > 0
            else (prior_high - float(row["close"])) / atr
        )
        if not (
            close_location >= self.MIN_REJECTION_CLOSE_LOCATION
            and volume_burst >= self.MIN_REJECTION_VOLUME_BURST
            and reclaim >= self.MIN_RECLAIM_ATR
        ):
            return False

        continuation_side = -reversal_side
        sweep_extreme = float(row["low"] if reversal_side > 0 else row["high"])
        boundary = prior_low if reversal_side > 0 else prior_high
        details = {
            "fair_value": fair_value,
            "dispersion": dispersion,
            "prior_high": prior_high,
            "prior_low": prior_low,
            "auction_efficiency_240": efficiency,
            "rejection_volume_burst": volume_burst,
            "rejection_close_location": close_location,
            "reclaim_atr": reclaim,
        }
        self.excess_probe = ExcessProbe(
            created_index=self.bar_index,
            expires_index=self.bar_index + self.FAILURE_WINDOW,
            reversal_side=reversal_side,
            continuation_side=continuation_side,
            fair_value=fair_value,
            sweep_extreme=sweep_extreme,
            reclaimed_boundary=boundary,
            prior_high=prior_high,
            prior_low=prior_low,
            dispersion=dispersion,
            details=details,
        )
        self._event("REJECTION_PROBE_ARMED", SCENARIO, row, details)
        return True

    def _detect_trend_sweep(self, row: dict[str, float | int]) -> bool:
        return False

    def _advance_probe(self, row: dict[str, float | int]) -> None:
        probe = self.excess_probe
        if probe is None or self.bar_index <= probe.created_index:
            return
        if self.bar_index > probe.expires_index:
            self._event("REJECTION_PROBE_EXPIRED", SCENARIO, row, probe.details)
            self.excess_probe = None
            return

        fair_value_hit = (
            float(row["high"]) >= probe.fair_value
            if probe.reversal_side > 0
            else float(row["low"]) <= probe.fair_value
        )
        if fair_value_hit:
            self._event("REJECTION_SUCCEEDED_NO_CONTINUATION", SCENARIO, row, probe.details)
            self.excess_probe = None
            return

        side = probe.continuation_side
        failure_hit = (
            float(row["close"]) < probe.sweep_extreme
            if side < 0
            else float(row["close"]) > probe.sweep_extreme
        )
        if not failure_hit:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            self.excess_probe = None
            return
        body = side * (float(row["close"]) - float(row["open"])) / atr
        volume_burst = self._volume_burst()
        close_location = self._close_location(row, side)
        if not (
            body >= self.FAILURE_BODY_ATR
            and volume_burst >= self.FAILURE_VOLUME_BURST
            and close_location >= self.FAILURE_CLOSE_LOCATION
        ):
            return

        if not self._entry_gate_open() or self.entry_pending or not self.portfolio.is_flat(
            self.config.instrument_id,
        ):
            self._event("ACCEPTANCE_CONFIRMED_BUT_OCCUPIED", SCENARIO, row, probe.details)
            self.excess_probe = None
            return

        range_width = probe.prior_high - probe.prior_low
        measured_target = probe.sweep_extreme + side * range_width
        setup = PendingSetup(
            scenario=SCENARIO,
            side=side,
            created_index=probe.created_index,
            expires_index=self.bar_index,
            extreme=probe.reclaimed_boundary,
            structure=probe.sweep_extreme,
            atr=atr,
            target_reference=measured_target,
            details=dict(probe.details),
        )
        details = {
            **probe.details,
            "failure_body_atr": body,
            "failure_volume_burst": volume_burst,
            "failure_close_location": close_location,
            "sweep_extreme": probe.sweep_extreme,
            "reclaimed_boundary": probe.reclaimed_boundary,
            "measured_target": measured_target,
            "failure_delay_bars": self.bar_index - probe.created_index,
        }
        self._event("REJECTION_FAILURE_ACCEPTED", SCENARIO, row, details)
        submitted = LiquidityTransitionStrategy._submit_bracket(
            self,
            setup,
            row,
            self.TARGET_NET_R,
            details,
        )
        if not submitted:
            self._event("ACCEPTANCE_EXECUTION_REJECTED", SCENARIO, row, details)
        self.excess_probe = None


__all__ = [
    "AuctionExcessFailureContinuationStrategy",
    "ExcessProbe",
    "LiquidityTransitionConfig",
]
