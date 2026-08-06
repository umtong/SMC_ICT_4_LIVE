#!/usr/bin/env python3
"""Candidate-04 v11: dual competing-risk auction transition.

The first random BTC week showed two economically different rejection failures:

* immediate low-impact acceptance: price crossed the sweep extreme within three
  bars, using no more aggressive volume than the original rejection and a
  sub-ATR confirmation body;
* high-impact or stale acceptance: continuation required increasing impact or
  arrived only after the original auction information had decayed.

V11 does not discard the second state. It treats it as a new probe. If its
measured continuation target is reached first, no reversal is taken. If price
instead closes back through the reclaimed liquidity boundary, the apparent
acceptance has itself failed and a second-order reversal toward the pre-event
fair value becomes tradable.

All matching, contingent orders, fees, positions, margin and NAV remain inside
NautilusTrader.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from nt_auction_failure_strategy import AuctionExcessFailureContinuationStrategy
from nt_auction_failure_strategy import ExcessProbe
from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup


DIRECT_SCENARIO = "LOW_IMPACT_ACCEPTANCE_CONTINUATION"
REFAILURE_SCENARIO = "ACCEPTANCE_REFAILURE_REVERSAL"


@dataclass(slots=True)
class AcceptanceProbe:
    continuation_side: int
    reversal_side: int
    created_index: int
    expires_index: int
    fair_value: float
    measured_target: float
    sweep_extreme: float
    reclaimed_boundary: float
    extension_extreme: float
    details: dict[str, Any]


class DualRiskAuctionStrategy(AuctionExcessFailureContinuationStrategy):
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.0
    DIRECT_MAX_BODY_ATR = 1.0
    ACCEPTANCE_PROBE_BARS = 30
    REFAILURE_BODY_ATR = 0.35
    REFAILURE_VOLUME_BURST = 1.10
    REFAILURE_CLOSE_LOCATION = 0.65
    DIRECT_TARGET_NET_R = 1.60
    REFAILURE_TARGET_NET_R = 1.20

    def __init__(self, config: LiquidityTransitionConfig) -> None:
        super().__init__(config)
        self.acceptance_probe: AcceptanceProbe | None = None

    def on_bar(self, bar: object) -> None:
        super().on_bar(bar)
        if self.bars:
            self._advance_acceptance_probe(self.bars[-1])

    def _detect_session_sweep(self, row: dict[str, float | int]) -> bool:
        if self.acceptance_probe is not None:
            return False
        return super()._detect_session_sweep(row)

    def _entry_gate_open(self, row: dict[str, float | int]) -> bool:
        ts_event = int(row["ts"])
        return (
            self._in_evaluation(ts_event)
            and not self._funding_blackout(ts_event)
            and not self.entry_pending
            and self.portfolio.is_flat(self.config.instrument_id)
        )

    def _advance_probe(self, row: dict[str, float | int]) -> None:
        probe: ExcessProbe | None = self.excess_probe
        if probe is None or self.bar_index <= probe.created_index:
            return
        if self.bar_index > probe.expires_index:
            self._event("REJECTION_PROBE_EXPIRED", DIRECT_SCENARIO, row, probe.details)
            self.excess_probe = None
            return

        fair_value_hit = (
            float(row["high"]) >= probe.fair_value
            if probe.reversal_side > 0
            else float(row["low"]) <= probe.fair_value
        )
        if fair_value_hit:
            self._event(
                "REJECTION_SUCCEEDED_NO_CONTINUATION",
                DIRECT_SCENARIO,
                row,
                probe.details,
            )
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
        failure_volume = self._volume_burst()
        close_location = self._close_location(row, side)
        if not (
            body >= self.FAILURE_BODY_ATR
            and failure_volume >= self.FAILURE_VOLUME_BURST
            and close_location >= self.FAILURE_CLOSE_LOCATION
        ):
            return

        rejection_volume = float(probe.details.get("rejection_volume_burst", 0.0))
        volume_ratio = (
            failure_volume / rejection_volume
            if rejection_volume > 0.0
            else math.inf
        )
        delay = self.bar_index - probe.created_index
        range_width = probe.prior_high - probe.prior_low
        measured_target = probe.sweep_extreme + side * range_width
        details: dict[str, Any] = {
            **probe.details,
            "failure_body_atr": body,
            "failure_volume_burst": failure_volume,
            "failure_close_location": close_location,
            "failure_to_rejection_volume_ratio": volume_ratio,
            "failure_delay_bars": delay,
            "sweep_extreme": probe.sweep_extreme,
            "reclaimed_boundary": probe.reclaimed_boundary,
            "measured_target": measured_target,
        }
        direct = (
            delay <= self.DIRECT_MAX_DELAY_BARS
            and volume_ratio <= self.DIRECT_MAX_VOLUME_RATIO
            and body <= self.DIRECT_MAX_BODY_ATR
        )

        if direct:
            if not self._entry_gate_open(row):
                self._event(
                    "DIRECT_ACCEPTANCE_CONFIRMED_BUT_OCCUPIED",
                    DIRECT_SCENARIO,
                    row,
                    details,
                )
                self.excess_probe = None
                return
            setup = PendingSetup(
                scenario=DIRECT_SCENARIO,
                side=side,
                created_index=probe.created_index,
                expires_index=self.bar_index,
                extreme=probe.reclaimed_boundary,
                structure=probe.sweep_extreme,
                atr=atr,
                target_reference=measured_target,
                details=dict(details),
            )
            self._event("LOW_IMPACT_ACCEPTANCE_CONFIRMED", DIRECT_SCENARIO, row, details)
            submitted = LiquidityTransitionStrategy._submit_bracket(
                self,
                setup,
                row,
                self.DIRECT_TARGET_NET_R,
                details,
            )
            if not submitted:
                self._event("DIRECT_ACCEPTANCE_EXECUTION_REJECTED", DIRECT_SCENARIO, row, details)
            self.excess_probe = None
            return

        extension = float(row["high"] if side > 0 else row["low"])
        self.acceptance_probe = AcceptanceProbe(
            continuation_side=side,
            reversal_side=-side,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.ACCEPTANCE_PROBE_BARS,
            fair_value=probe.fair_value,
            measured_target=measured_target,
            sweep_extreme=probe.sweep_extreme,
            reclaimed_boundary=probe.reclaimed_boundary,
            extension_extreme=extension,
            details=details,
        )
        self._event("HIGH_IMPACT_ACCEPTANCE_PROBE_ARMED", REFAILURE_SCENARIO, row, details)
        self.excess_probe = None

    def _advance_acceptance_probe(self, row: dict[str, float | int]) -> None:
        probe = self.acceptance_probe
        if probe is None or self.bar_index <= probe.created_index:
            return
        side = probe.continuation_side
        if side > 0:
            probe.extension_extreme = max(probe.extension_extreme, float(row["high"]))
            target_hit = float(row["high"]) >= probe.measured_target
            refailure_close = float(row["close"]) < probe.reclaimed_boundary
        else:
            probe.extension_extreme = min(probe.extension_extreme, float(row["low"]))
            target_hit = float(row["low"]) <= probe.measured_target
            refailure_close = float(row["close"]) > probe.reclaimed_boundary

        if self.bar_index > probe.expires_index:
            self._event("HIGH_IMPACT_ACCEPTANCE_EXPIRED", REFAILURE_SCENARIO, row, probe.details)
            self.acceptance_probe = None
            return
        if target_hit and refailure_close:
            # Intrabar ordering is unknowable from a completed OHLC bar. Do not
            # manufacture a favorable sequence.
            self._event("ACCEPTANCE_COMPETING_RISK_AMBIGUOUS", REFAILURE_SCENARIO, row, probe.details)
            self.acceptance_probe = None
            return
        if target_hit:
            self._event("HIGH_IMPACT_ACCEPTANCE_SUCCEEDED", REFAILURE_SCENARIO, row, probe.details)
            self.acceptance_probe = None
            return
        if not refailure_close:
            return

        reversal_side = probe.reversal_side
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            self.acceptance_probe = None
            return
        body = reversal_side * (float(row["close"]) - float(row["open"])) / atr
        volume_burst = self._volume_burst()
        close_location = self._close_location(row, reversal_side)
        details = {
            **probe.details,
            "refailure_body_atr": body,
            "refailure_volume_burst": volume_burst,
            "refailure_close_location": close_location,
            "acceptance_probe_delay_bars": self.bar_index - probe.created_index,
            "acceptance_extension_extreme": probe.extension_extreme,
        }
        if not (
            body >= self.REFAILURE_BODY_ATR
            and volume_burst >= self.REFAILURE_VOLUME_BURST
            and close_location >= self.REFAILURE_CLOSE_LOCATION
        ):
            return
        if not self._entry_gate_open(row):
            self._event("REFAILURE_CONFIRMED_BUT_OCCUPIED", REFAILURE_SCENARIO, row, details)
            self.acceptance_probe = None
            return

        setup = PendingSetup(
            scenario=REFAILURE_SCENARIO,
            side=reversal_side,
            created_index=probe.created_index,
            expires_index=self.bar_index,
            extreme=probe.extension_extreme,
            structure=probe.reclaimed_boundary,
            atr=atr,
            target_reference=probe.fair_value,
            details=dict(details),
        )
        self._event("ACCEPTANCE_REFAILURE_CONFIRMED", REFAILURE_SCENARIO, row, details)
        submitted = LiquidityTransitionStrategy._submit_bracket(
            self,
            setup,
            row,
            self.REFAILURE_TARGET_NET_R,
            details,
        )
        if not submitted:
            self._event("REFAILURE_EXECUTION_REJECTED", REFAILURE_SCENARIO, row, details)
        self.acceptance_probe = None


__all__ = [
    "AcceptanceProbe",
    "DualRiskAuctionStrategy",
    "LiquidityTransitionConfig",
]
