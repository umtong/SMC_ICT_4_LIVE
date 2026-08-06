#!/usr/bin/env python3
"""Acceptance-retest continuation for candidate-04.

This is a complete scenario, not a filter on the V18c entry:

    statistically extreme rejection
      -> rejection failure and close beyond the sweep extreme
      -> displacement imbalance or broken-extreme support/resistance
      -> passive-liquidity refill retests that zone without reclaiming the old
         range or reaching the failed-auction fair value
      -> renewed directional displacement beyond the acceptance close
      -> pre-existing external liquidity or pre-event dealing-range target

The entry deliberately occurs only after a retest and renewed displacement. It
therefore tests resiliency/persistence rather than chasing the impact bar. All
orders, fills, fees, portfolio accounting and NAV remain in NautilusTrader.
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
from nt_liquidity_strategy import net_r_at_price
from nt_low_impact_external_strategy import LowImpactExternalLiquidityStrategy
from nt_low_impact_external_strategy import choose_external_liquidity_target


SCENARIO = "ACCEPTANCE_RETEST_LIQUIDITY_CONTINUATION"


@dataclass(slots=True)
class AcceptanceRetest:
    probe: ExcessProbe
    side: int
    accepted_index: int
    expires_index: int
    acceptance_close: float
    acceptance_high: float
    acceptance_low: float
    acceptance_atr: float
    failure_volume_burst: float
    failure_body_atr: float
    failure_close_location: float
    failure_volume_ratio: float
    failure_delay_bars: int
    fvg_low: float | None
    fvg_high: float | None
    retest_seen: bool = False
    retest_index: int = -1
    retest_extreme: float = math.nan
    retest_source: str | None = None


class AcceptanceRetestStrategy(LowImpactExternalLiquidityStrategy):
    """External 30-minute acceptance followed by a causal refill/retest."""

    RETEST_MODE = "fvg"
    RETEST_WINDOW = 30
    MIN_TARGET_NET_R = 1.20

    def __init__(self, config: LiquidityTransitionConfig) -> None:
        super().__init__(config)
        self.acceptance_retest: AcceptanceRetest | None = None

    def _detect_session_sweep(self, row: dict[str, float | int]) -> bool:
        if self.acceptance_retest is not None:
            return False
        return AuctionExcessFailureContinuationStrategy._detect_session_sweep(self, row)

    def _advance_probe(self, row: dict[str, float | int]) -> None:
        if self.acceptance_retest is not None:
            self._advance_retest(row)
            return

        probe: ExcessProbe | None = self.excess_probe
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
        accepted = (
            float(row["close"]) < probe.sweep_extreme
            if side < 0
            else float(row["close"]) > probe.sweep_extreme
        )
        if not accepted:
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
        rows = list(self.bars)
        fvg_low: float | None = None
        fvg_high: float | None = None
        if len(rows) >= 3:
            two_back = rows[-3]
            if side > 0 and float(row["low"]) > float(two_back["high"]):
                fvg_low = float(two_back["high"])
                fvg_high = float(row["low"])
            elif side < 0 and float(row["high"]) < float(two_back["low"]):
                fvg_low = float(row["high"])
                fvg_high = float(two_back["low"])

        if self.RETEST_MODE == "fvg" and fvg_low is None:
            self._event(
                "ACCEPTANCE_WITHOUT_DISPLACEMENT_FVG",
                SCENARIO,
                row,
                {
                    **probe.details,
                    "failure_body_atr": body,
                    "failure_volume_burst": failure_volume,
                    "failure_close_location": close_location,
                    "failure_to_rejection_volume_ratio": volume_ratio,
                    "failure_delay_bars": self.bar_index - probe.created_index,
                },
            )
            self.excess_probe = None
            return

        state = AcceptanceRetest(
            probe=probe,
            side=side,
            accepted_index=self.bar_index,
            expires_index=self.bar_index + self.RETEST_WINDOW,
            acceptance_close=float(row["close"]),
            acceptance_high=float(row["high"]),
            acceptance_low=float(row["low"]),
            acceptance_atr=atr,
            failure_volume_burst=failure_volume,
            failure_body_atr=body,
            failure_close_location=close_location,
            failure_volume_ratio=volume_ratio,
            failure_delay_bars=self.bar_index - probe.created_index,
            fvg_low=fvg_low,
            fvg_high=fvg_high,
        )
        self.acceptance_retest = state
        self.excess_probe = None
        self._event(
            "ACCEPTANCE_RETEST_ARMED",
            SCENARIO,
            row,
            self._state_details(state),
        )

    def _state_details(self, state: AcceptanceRetest) -> dict[str, Any]:
        return {
            **state.probe.details,
            "sweep_extreme": state.probe.sweep_extreme,
            "reclaimed_boundary": state.probe.reclaimed_boundary,
            "failure_body_atr": state.failure_body_atr,
            "failure_volume_burst": state.failure_volume_burst,
            "failure_close_location": state.failure_close_location,
            "failure_to_rejection_volume_ratio": state.failure_volume_ratio,
            "failure_delay_bars": state.failure_delay_bars,
            "acceptance_close": state.acceptance_close,
            "acceptance_high": state.acceptance_high,
            "acceptance_low": state.acceptance_low,
            "acceptance_atr": state.acceptance_atr,
            "fvg_low": state.fvg_low,
            "fvg_high": state.fvg_high,
            "retest_mode": self.RETEST_MODE,
            "retest_window": self.RETEST_WINDOW,
            "retest_seen": state.retest_seen,
            "retest_index": state.retest_index,
            "retest_extreme": state.retest_extreme,
            "retest_source": state.retest_source,
        }

    def _invalidated(self, state: AcceptanceRetest, row: dict[str, float | int]) -> str | None:
        side = state.side
        boundary_reclaimed = (
            float(row["close"]) <= state.probe.reclaimed_boundary
            if side > 0
            else float(row["close"]) >= state.probe.reclaimed_boundary
        )
        if boundary_reclaimed:
            return "OLD_RANGE_RECLAIMED"
        fair_value_hit = (
            float(row["low"]) <= state.probe.fair_value
            if side > 0
            else float(row["high"]) >= state.probe.fair_value
        )
        if fair_value_hit:
            return "FAILED_AUCTION_FAIR_VALUE_REACHED"
        return None

    def _touches_retest_zone(
        self,
        state: AcceptanceRetest,
        row: dict[str, float | int],
    ) -> str | None:
        side = state.side
        accepted_side_close = (
            float(row["close"]) > state.probe.sweep_extreme
            if side > 0
            else float(row["close"]) < state.probe.sweep_extreme
        )
        if not accepted_side_close:
            return None

        if self.RETEST_MODE in ("fvg", "fvg_or_level") and state.fvg_low is not None:
            overlaps = (
                float(row["low"]) <= state.fvg_high
                and float(row["high"]) >= state.fvg_low
            )
            if overlaps:
                return "DISPLACEMENT_FVG"

        if self.RETEST_MODE in ("level", "fvg_or_level"):
            level_touched = (
                float(row["low"]) <= state.probe.sweep_extreme
                if side > 0
                else float(row["high"]) >= state.probe.sweep_extreme
            )
            if level_touched:
                return "BROKEN_SWEEP_EXTREME"
        return None

    def _advance_retest(self, row: dict[str, float | int]) -> None:
        state = self.acceptance_retest
        if state is None or self.bar_index <= state.accepted_index:
            return
        if self.bar_index > state.expires_index:
            self._event("ACCEPTANCE_RETEST_EXPIRED", SCENARIO, row, self._state_details(state))
            self.acceptance_retest = None
            return
        invalidation = self._invalidated(state, row)
        if invalidation is not None:
            self._event(
                "ACCEPTANCE_RETEST_INVALIDATED",
                SCENARIO,
                row,
                {**self._state_details(state), "invalidation": invalidation},
            )
            self.acceptance_retest = None
            return
        if self._funding_blackout(int(row["ts"])):
            self._event(
                "ACCEPTANCE_RETEST_FUNDING_INVALIDATED",
                SCENARIO,
                row,
                self._state_details(state),
            )
            self.acceptance_retest = None
            return

        if not state.retest_seen:
            source = self._touches_retest_zone(state, row)
            if source is None:
                return
            state.retest_seen = True
            state.retest_index = self.bar_index
            state.retest_source = source
            state.retest_extreme = float(row["low"] if state.side > 0 else row["high"])
            self._event("ACCEPTANCE_RETEST_TOUCHED", SCENARIO, row, self._state_details(state))
            return

        if self.bar_index <= state.retest_index:
            return
        state.retest_extreme = (
            min(state.retest_extreme, float(row["low"]))
            if state.side > 0
            else max(state.retest_extreme, float(row["high"]))
        )
        renewed = (
            float(row["close"]) > state.acceptance_close
            if state.side > 0
            else float(row["close"]) < state.acceptance_close
        )
        if not renewed:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            self.acceptance_retest = None
            return
        body = state.side * (float(row["close"]) - float(row["open"])) / atr
        volume_burst = self._volume_burst()
        close_location = self._close_location(row, state.side)
        if not (
            body >= self.FAILURE_BODY_ATR
            and volume_burst >= self.FAILURE_VOLUME_BURST
            and close_location >= self.FAILURE_CLOSE_LOCATION
        ):
            self._event(
                "ACCEPTANCE_RETEST_WEAK_RENEWAL",
                SCENARIO,
                row,
                {
                    **self._state_details(state),
                    "renewal_body_atr": body,
                    "renewal_volume_burst": volume_burst,
                    "renewal_close_location": close_location,
                },
            )
            self.acceptance_retest = None
            return
        if not self._entry_gate_open(row):
            self._event("ACCEPTANCE_RETEST_OCCUPIED", SCENARIO, row, self._state_details(state))
            self.acceptance_retest = None
            return

        entry = float(row["close"])
        stop = state.probe.reclaimed_boundary - state.side * self.config.stop_buffer_atr * atr
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        price_loss = state.side * (entry - stop)
        planned_loss = price_loss + cost_rate * (entry + stop)
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.acceptance_retest = None
            return

        external = choose_external_liquidity_target(
            self._external_levels(state.side),
            entry=entry,
            stop=stop,
            side=state.side,
            cost_rate=cost_rate,
            minimum_net_r=self.MIN_TARGET_NET_R,
        )
        if external is not None:
            target = external.price
            source = external.source
            reference_net_r = external.net_r
        else:
            range_width = state.probe.prior_high - state.probe.prior_low
            target = state.probe.sweep_extreme + state.side * range_width
            reference_net_r = net_r_at_price(
                entry,
                target,
                state.side,
                planned_loss,
                cost_rate,
            )
            source = "pre_event_30m_dealing_range_projection"
            if reference_net_r < self.MIN_TARGET_NET_R:
                self._event(
                    "ACCEPTANCE_RETEST_NO_CAUSAL_TARGET",
                    SCENARIO,
                    row,
                    {
                        **self._state_details(state),
                        "measured_target": target,
                        "measured_target_net_r": reference_net_r,
                    },
                )
                self.acceptance_retest = None
                return

        setup = PendingSetup(
            scenario=SCENARIO,
            side=state.side,
            created_index=state.accepted_index,
            expires_index=self.bar_index,
            extreme=state.probe.reclaimed_boundary,
            structure=state.acceptance_close,
            atr=atr,
            target_reference=target,
            details=self._state_details(state),
        )
        details = {
            **self._state_details(state),
            "renewal_body_atr": body,
            "renewal_volume_burst": volume_burst,
            "renewal_close_location": close_location,
            "causal_target": target,
            "causal_target_source": source,
            "causal_target_net_r_at_confirmation": reference_net_r,
            "minimum_target_net_r": self.MIN_TARGET_NET_R,
        }
        self._event("ACCEPTANCE_RETEST_CONFIRMED", SCENARIO, row, details)
        submitted = LiquidityTransitionStrategy._submit_bracket(
            self,
            setup,
            row,
            self.MIN_TARGET_NET_R,
            details,
        )
        if not submitted:
            self._event("ACCEPTANCE_RETEST_EXECUTION_REJECTED", SCENARIO, row, details)
        self.acceptance_retest = None


class ExternalFvgRetestStrategy(AcceptanceRetestStrategy):
    RETEST_MODE = "fvg"


class ExternalLevelRetestStrategy(AcceptanceRetestStrategy):
    RETEST_MODE = "level"


class ExternalFvgOrLevelRetestStrategy(AcceptanceRetestStrategy):
    RETEST_MODE = "fvg_or_level"


__all__ = [
    "AcceptanceRetest",
    "AcceptanceRetestStrategy",
    "ExternalFvgOrLevelRetestStrategy",
    "ExternalFvgRetestStrategy",
    "ExternalLevelRetestStrategy",
]
