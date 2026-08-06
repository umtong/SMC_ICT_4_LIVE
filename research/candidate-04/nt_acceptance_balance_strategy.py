#!/usr/bin/env python3
"""Candidate-04 v13: accepted auction -> balance -> redistribution.

V12 proved that prompt low-impact acceptance toward a known external liquidity
pool can be highly profitable, but it is sparse. V13 preserves that branch and
adds a separate causal scenario for accepted auctions which cannot be entered
immediately:

1. an apparent liquidity rejection fails before fair value is revisited;
2. price remains outside the accepted sweep extreme for several completed bars;
3. those bars compress into a bounded balance rather than continuing as a
   one-bar liquidation impulse;
4. price redistributes from the frozen balance with body, participation and
   directional close confirmation;
5. the target is the nearest pre-breakout external liquidity pool when one
   exists, otherwise one full frozen-balance range projection.

The balance is frozen before the breakout bar. A close through the reclaimed
liquidity boundary invalidates the state. All fills, fees, positions, margin,
liquidation and NAV are handled only by NautilusTrader.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from nt_auction_failure_strategy import ExcessProbe
from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup
from nt_liquidity_strategy import net_r_at_price
from nt_low_impact_external_strategy import LowImpactExternalLiquidityStrategy
from nt_low_impact_external_strategy import choose_external_liquidity_target


DIRECT_SCENARIO = "LOW_IMPACT_EXTERNAL_LIQUIDITY_CONTINUATION"
BALANCE_SCENARIO = "ACCEPTED_AUCTION_BALANCE_REDISTRIBUTION"


@dataclass(slots=True)
class BalanceProbe:
    side: int
    created_index: int
    expires_index: int
    sweep_extreme: float
    reclaimed_boundary: float
    fair_value: float
    details: dict[str, Any]
    consecutive_outside: list[tuple[int, float, float, float]]
    balance_high: float | None = None
    balance_low: float | None = None
    ready_index: int | None = None


class AcceptanceBalanceStrategy(LowImpactExternalLiquidityStrategy):
    """Base class; subclasses control only the causal balance horizon."""

    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.10
    DIRECT_MAX_BODY_ATR = 1.0

    BALANCE_BARS = 5
    BALANCE_EXPIRES_BARS = 40
    MAX_BALANCE_WIDTH_ATR = 2.0
    BREAKOUT_BODY_ATR = 0.35
    BREAKOUT_VOLUME_BURST = 1.10
    BREAKOUT_CLOSE_LOCATION = 0.65
    BREAKOUT_DISTANCE_ATR = 0.03
    MIN_TARGET_NET_R = 1.20
    PROJECTED_RANGE_MULTIPLE = 1.0

    def __init__(self, config: LiquidityTransitionConfig) -> None:
        super().__init__(config)
        self.balance_probe: BalanceProbe | None = None

    def on_bar(self, bar: object) -> None:
        super().on_bar(bar)
        if self.bars:
            self._advance_balance_probe(self.bars[-1])

    def _detect_session_sweep(self, row: dict[str, float | int]) -> bool:
        if self.balance_probe is not None:
            return False
        return super()._detect_session_sweep(row)

    def _arm_balance(
        self,
        probe: ExcessProbe,
        details: dict[str, Any],
    ) -> None:
        self.balance_probe = BalanceProbe(
            side=probe.continuation_side,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.BALANCE_EXPIRES_BARS,
            sweep_extreme=probe.sweep_extreme,
            reclaimed_boundary=probe.reclaimed_boundary,
            fair_value=probe.fair_value,
            details={
                **details,
                "balance_bars_required": self.BALANCE_BARS,
                "max_balance_width_atr": self.MAX_BALANCE_WIDTH_ATR,
            },
            consecutive_outside=[],
        )
        self._event(
            "ACCEPTANCE_BALANCE_PROBE_ARMED",
            BALANCE_SCENARIO,
            self.bars[-1],
            self.balance_probe.details,
        )

    def _advance_probe(self, row: dict[str, float | int]) -> None:
        probe: ExcessProbe | None = self.excess_probe
        if probe is None or self.bar_index <= probe.created_index:
            return
        if self.bar_index > probe.expires_index:
            self._event("REJECTION_PROBE_EXPIRED", BALANCE_SCENARIO, row, probe.details)
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
                BALANCE_SCENARIO,
                row,
                probe.details,
            )
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
        delay = self.bar_index - probe.created_index
        details: dict[str, Any] = {
            **probe.details,
            "failure_body_atr": body,
            "failure_volume_burst": failure_volume,
            "failure_close_location": close_location,
            "failure_to_rejection_volume_ratio": volume_ratio,
            "failure_delay_bars": delay,
            "sweep_extreme": probe.sweep_extreme,
            "reclaimed_boundary": probe.reclaimed_boundary,
        }
        direct = (
            delay <= self.DIRECT_MAX_DELAY_BARS
            and volume_ratio <= self.DIRECT_MAX_VOLUME_RATIO
            and body <= self.DIRECT_MAX_BODY_ATR
        )

        if direct and self._entry_gate_open(row):
            entry = float(row["close"])
            stop = probe.reclaimed_boundary - side * self.config.stop_buffer_atr * atr
            cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
            target = choose_external_liquidity_target(
                self._external_levels(side),
                entry=entry,
                stop=stop,
                side=side,
                cost_rate=cost_rate,
                minimum_net_r=self.MIN_EXTERNAL_TARGET_NET_R,
            )
            if target is not None:
                setup = PendingSetup(
                    scenario=DIRECT_SCENARIO,
                    side=side,
                    created_index=probe.created_index,
                    expires_index=self.bar_index,
                    extreme=probe.reclaimed_boundary,
                    structure=probe.sweep_extreme,
                    atr=atr,
                    target_reference=target.price,
                    details=dict(details),
                )
                routed = {
                    **details,
                    "external_target": target.price,
                    "external_target_source": target.source,
                    "external_target_net_r_at_confirmation": target.net_r,
                    "minimum_external_target_net_r": self.MIN_EXTERNAL_TARGET_NET_R,
                }
                self._event(
                    "LOW_IMPACT_EXTERNAL_TARGET_CONFIRMED",
                    DIRECT_SCENARIO,
                    row,
                    routed,
                )
                submitted = LiquidityTransitionStrategy._submit_bracket(
                    self,
                    setup,
                    row,
                    self.TARGET_NET_R,
                    routed,
                )
                if not submitted:
                    self._event(
                        "EXTERNAL_TARGET_EXECUTION_REJECTED",
                        DIRECT_SCENARIO,
                        row,
                        routed,
                    )
                self.excess_probe = None
                return

        # Do not force an immediate trade. Both direct events without a target
        # and higher-impact/stale acceptance must establish a new balance first.
        self._arm_balance(probe, details)
        self.excess_probe = None

    def _advance_balance_probe(self, row: dict[str, float | int]) -> None:
        probe = self.balance_probe
        if probe is None or self.bar_index <= probe.created_index:
            return
        side = probe.side
        close = float(row["close"])
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            self.balance_probe = None
            return

        if self.bar_index > probe.expires_index:
            self._event("ACCEPTANCE_BALANCE_EXPIRED", BALANCE_SCENARIO, row, probe.details)
            self.balance_probe = None
            return
        if side * (close - probe.reclaimed_boundary) <= 0.0:
            self._event("ACCEPTANCE_BALANCE_INVALIDATED", BALANCE_SCENARIO, row, probe.details)
            self.balance_probe = None
            return

        if probe.ready_index is None:
            fully_outside = side * (close - probe.sweep_extreme) > 0.0
            if not fully_outside:
                probe.consecutive_outside.clear()
                return
            probe.consecutive_outside.append(
                (
                    self.bar_index,
                    float(row["high"]),
                    float(row["low"]),
                    close,
                ),
            )
            if len(probe.consecutive_outside) > self.BALANCE_BARS:
                probe.consecutive_outside.pop(0)
            if len(probe.consecutive_outside) < self.BALANCE_BARS:
                return
            high = max(item[1] for item in probe.consecutive_outside)
            low = min(item[2] for item in probe.consecutive_outside)
            width_atr = (high - low) / atr
            if width_atr > self.MAX_BALANCE_WIDTH_ATR:
                return
            probe.balance_high = high
            probe.balance_low = low
            probe.ready_index = self.bar_index
            self._event(
                "ACCEPTANCE_BALANCE_FROZEN",
                BALANCE_SCENARIO,
                row,
                {
                    **probe.details,
                    "balance_high": high,
                    "balance_low": low,
                    "balance_width_atr": width_atr,
                    "balance_ready_index": self.bar_index,
                },
            )
            return

        assert probe.balance_high is not None
        assert probe.balance_low is not None
        if self.bar_index <= probe.ready_index:
            return

        opposite_break = (
            close < probe.balance_low
            if side > 0
            else close > probe.balance_high
        )
        if opposite_break:
            self._event("BALANCE_REDISTRIBUTION_FAILED", BALANCE_SCENARIO, row, probe.details)
            self.balance_probe = None
            return

        boundary = probe.balance_high if side > 0 else probe.balance_low
        breakout = side * (close - boundary) >= self.BREAKOUT_DISTANCE_ATR * atr
        if not breakout:
            return
        body = side * (close - float(row["open"])) / atr
        volume_burst = self._volume_burst()
        close_location = self._close_location(row, side)
        if not (
            body >= self.BREAKOUT_BODY_ATR
            and volume_burst >= self.BREAKOUT_VOLUME_BURST
            and close_location >= self.BREAKOUT_CLOSE_LOCATION
        ):
            return
        if not self._entry_gate_open(row):
            self._event("BALANCE_BREAKOUT_CONFIRMED_BUT_OCCUPIED", BALANCE_SCENARIO, row, probe.details)
            self.balance_probe = None
            return

        stop_edge = probe.balance_low if side > 0 else probe.balance_high
        estimated_stop = stop_edge - side * self.config.stop_buffer_atr * atr
        entry = close
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        price_loss = side * (entry - estimated_stop)
        planned_loss = price_loss + cost_rate * (entry + estimated_stop)
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.balance_probe = None
            return

        external = choose_external_liquidity_target(
            self._external_levels(side),
            entry=entry,
            stop=estimated_stop,
            side=side,
            cost_rate=cost_rate,
            minimum_net_r=self.MIN_TARGET_NET_R,
        )
        balance_width = probe.balance_high - probe.balance_low
        projected_target = boundary + side * self.PROJECTED_RANGE_MULTIPLE * balance_width
        projected_net_r = net_r_at_price(
            entry,
            projected_target,
            side,
            planned_loss,
            cost_rate,
        )
        if external is not None:
            target = external.price
            target_source = external.source
            target_net_r = external.net_r
        elif projected_net_r >= self.MIN_TARGET_NET_R:
            target = projected_target
            target_source = "frozen_balance_range_projection"
            target_net_r = projected_net_r
        else:
            self._event(
                "NO_REDISTRIBUTION_TARGET",
                BALANCE_SCENARIO,
                row,
                {
                    **probe.details,
                    "projected_target": projected_target,
                    "projected_target_net_r": projected_net_r,
                    "minimum_target_net_r": self.MIN_TARGET_NET_R,
                },
            )
            self.balance_probe = None
            return

        setup = PendingSetup(
            scenario=BALANCE_SCENARIO,
            side=side,
            created_index=probe.created_index,
            expires_index=self.bar_index,
            extreme=stop_edge,
            structure=boundary,
            atr=atr,
            target_reference=target,
            details=dict(probe.details),
        )
        details = {
            **probe.details,
            "balance_high": probe.balance_high,
            "balance_low": probe.balance_low,
            "balance_width": balance_width,
            "breakout_boundary": boundary,
            "breakout_body_atr": body,
            "breakout_volume_burst": volume_burst,
            "breakout_close_location": close_location,
            "redistribution_target": target,
            "redistribution_target_source": target_source,
            "redistribution_target_net_r": target_net_r,
        }
        self._event("BALANCE_REDISTRIBUTION_CONFIRMED", BALANCE_SCENARIO, row, details)
        submitted = LiquidityTransitionStrategy._submit_bracket(
            self,
            setup,
            row,
            self.MIN_TARGET_NET_R,
            details,
        )
        if not submitted:
            self._event("BALANCE_REDISTRIBUTION_EXECUTION_REJECTED", BALANCE_SCENARIO, row, details)
        self.balance_probe = None


class AcceptanceBalance3Strategy(AcceptanceBalanceStrategy):
    BALANCE_BARS = 3
    MAX_BALANCE_WIDTH_ATR = 1.50


class AcceptanceBalance5Strategy(AcceptanceBalanceStrategy):
    BALANCE_BARS = 5
    MAX_BALANCE_WIDTH_ATR = 2.00


class AcceptanceBalance8Strategy(AcceptanceBalanceStrategy):
    BALANCE_BARS = 8
    MAX_BALANCE_WIDTH_ATR = 2.50


__all__ = [
    "AcceptanceBalance3Strategy",
    "AcceptanceBalance5Strategy",
    "AcceptanceBalance8Strategy",
    "BalanceProbe",
    "LiquidityTransitionConfig",
]
