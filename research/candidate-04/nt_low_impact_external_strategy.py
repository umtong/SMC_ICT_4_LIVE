#!/usr/bin/env python3
"""Candidate-04 v12: low-impact acceptance toward external liquidity.

The V11 second-order reversal branch is removed after controlled exits from
0.8R through 1.2R remained negative. This candidate retains only the causal
state which was profitable on the first BTC week:

1. a statistically extreme liquidity rejection occurs inside a rotational
   four-hour auction;
2. price accepts beyond the sweep extreme before returning to fair value;
3. acceptance arrives promptly and does not require materially more executed
   volume or a climactic body than the original rejection;
4. the next target is an actual pre-confirmation external liquidity level,
   represented by nested completed-bar highs/lows and the previous eight-hour
   session boundary.

The strategy does not manufacture a fixed R target. It selects the nearest
known directional liquidity level with at least 1.2R after costs. All orders,
fills, fees, positions, margin, liquidation and NAV remain inside
NautilusTrader.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
from typing import Iterable

from nt_auction_failure_strategy import AuctionExcessFailureContinuationStrategy
from nt_auction_failure_strategy import ExcessProbe
from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup
from nt_liquidity_strategy import net_r_at_price


SCENARIO = "LOW_IMPACT_EXTERNAL_LIQUIDITY_CONTINUATION"
TARGET_WINDOWS = (30, 60, 120, 240, 480, 720)


@dataclass(frozen=True, slots=True)
class LiquidityTarget:
    price: float
    source: str
    net_r: float


def choose_external_liquidity_target(
    levels: Iterable[tuple[str, float]],
    *,
    entry: float,
    stop: float,
    side: int,
    cost_rate: float,
    minimum_net_r: float,
) -> LiquidityTarget | None:
    """Choose the nearest directional external level meeting the cost floor."""

    price_loss = side * (entry - stop)
    if not math.isfinite(price_loss) or price_loss <= 0.0:
        return None
    planned_loss = price_loss + cost_rate * (entry + stop)
    if not math.isfinite(planned_loss) or planned_loss <= 0.0:
        return None

    unique: dict[float, str] = {}
    for source, raw_price in levels:
        price = float(raw_price)
        if not math.isfinite(price) or side * (price - entry) <= 0.0:
            continue
        unique.setdefault(price, source)

    ordered = sorted(unique.items(), key=lambda item: side * (item[0] - entry))
    for price, source in ordered:
        net_r = net_r_at_price(entry, price, side, planned_loss, cost_rate)
        if net_r >= minimum_net_r:
            return LiquidityTarget(price=price, source=source, net_r=net_r)
    return None


class LowImpactExternalLiquidityStrategy(AuctionExcessFailureContinuationStrategy):
    """Base implementation; subclasses control only acceptance-state tolerance."""

    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.0
    DIRECT_MAX_BODY_ATR = 1.0
    MIN_EXTERNAL_TARGET_NET_R = 1.20
    TARGET_NET_R = 1.60

    def _external_levels(self, side: int) -> list[tuple[str, float]]:
        rows = list(self.bars)
        # The confirmation bar is deliberately excluded. Every target must have
        # existed before the state was confirmed.
        history = rows[:-1]
        levels: list[tuple[str, float]] = []
        for window in TARGET_WINDOWS:
            if len(history) < window:
                continue
            selected = history[-window:]
            price = (
                max(float(item["high"]) for item in selected)
                if side > 0
                else min(float(item["low"]) for item in selected)
            )
            levels.append((f"rolling_{window}m_{'high' if side > 0 else 'low'}", price))

        session = self.previous_session
        if session is not None:
            levels.append(
                (
                    "previous_8h_session_boundary",
                    session.high if side > 0 else session.low,
                ),
            )
        return levels

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
            "direct_max_delay_bars": self.DIRECT_MAX_DELAY_BARS,
            "direct_max_volume_ratio": self.DIRECT_MAX_VOLUME_RATIO,
            "direct_max_body_atr": self.DIRECT_MAX_BODY_ATR,
        }
        accepted = (
            delay <= self.DIRECT_MAX_DELAY_BARS
            and volume_ratio <= self.DIRECT_MAX_VOLUME_RATIO
            and body <= self.DIRECT_MAX_BODY_ATR
        )
        if not accepted:
            self._event("HIGH_IMPACT_OR_STALE_ACCEPTANCE_SKIPPED", SCENARIO, row, details)
            self.excess_probe = None
            return
        if not self._entry_gate_open(row):
            self._event("LOW_IMPACT_ACCEPTANCE_OCCUPIED", SCENARIO, row, details)
            self.excess_probe = None
            return

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
        if target is None:
            self._event(
                "NO_EXTERNAL_LIQUIDITY_TARGET",
                SCENARIO,
                row,
                {
                    **details,
                    "estimated_entry": entry,
                    "estimated_stop": stop,
                    "minimum_external_target_net_r": self.MIN_EXTERNAL_TARGET_NET_R,
                },
            )
            self.excess_probe = None
            return

        setup = PendingSetup(
            scenario=SCENARIO,
            side=side,
            created_index=probe.created_index,
            expires_index=self.bar_index,
            extreme=probe.reclaimed_boundary,
            structure=probe.sweep_extreme,
            atr=atr,
            target_reference=target.price,
            details=dict(details),
        )
        routed_details = {
            **details,
            "external_target": target.price,
            "external_target_source": target.source,
            "external_target_net_r_at_confirmation": target.net_r,
            "minimum_external_target_net_r": self.MIN_EXTERNAL_TARGET_NET_R,
        }
        self._event("LOW_IMPACT_EXTERNAL_TARGET_CONFIRMED", SCENARIO, row, routed_details)
        submitted = LiquidityTransitionStrategy._submit_bracket(
            self,
            setup,
            row,
            self.TARGET_NET_R,
            routed_details,
        )
        if not submitted:
            self._event("EXTERNAL_TARGET_EXECUTION_REJECTED", SCENARIO, row, routed_details)
        self.excess_probe = None


class ExternalStrictStrategy(LowImpactExternalLiquidityStrategy):
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.0
    DIRECT_MAX_BODY_ATR = 1.0


class ExternalNearEqualStrategy(LowImpactExternalLiquidityStrategy):
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.10
    DIRECT_MAX_BODY_ATR = 1.0


class ExternalTimelyStrategy(LowImpactExternalLiquidityStrategy):
    DIRECT_MAX_DELAY_BARS = 5
    DIRECT_MAX_VOLUME_RATIO = 1.10
    DIRECT_MAX_BODY_ATR = 1.0


class ExternalBalancedStrategy(LowImpactExternalLiquidityStrategy):
    DIRECT_MAX_DELAY_BARS = 5
    DIRECT_MAX_VOLUME_RATIO = 1.25
    DIRECT_MAX_BODY_ATR = 1.25


__all__ = [
    "ExternalBalancedStrategy",
    "ExternalNearEqualStrategy",
    "ExternalStrictStrategy",
    "ExternalTimelyStrategy",
    "LiquidityTarget",
    "LiquidityTransitionConfig",
    "choose_external_liquidity_target",
]
