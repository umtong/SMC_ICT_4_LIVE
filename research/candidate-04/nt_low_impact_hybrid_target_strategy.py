#!/usr/bin/env python3
"""Candidate-04 v14: low-impact acceptance with causal target hierarchy.

V12 showed strong but sparse performance when a pre-existing external liquidity
pool was available. V9 showed that one low-impact new-price-discovery episode
without such a pool still completed the measured move of the pre-event dealing
range. V14 therefore uses a strict target hierarchy, not a fitted profit target:

1. nearest pre-confirmation external liquidity pool with sufficient cost-aware
   distance;
2. if none exists, one pre-event 30-minute dealing-range width projected from
   the accepted sweep extreme.

High-impact or stale acceptance remains skipped. No balance, refailure reversal
or fixed-R fallback is added. NautilusTrader owns every order, fill, fee,
position, margin, liquidation and NAV calculation.
"""
from __future__ import annotations

import math
from typing import Any

from nt_auction_failure_strategy import ExcessProbe
from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup
from nt_liquidity_strategy import net_r_at_price
from nt_low_impact_external_strategy import LowImpactExternalLiquidityStrategy
from nt_low_impact_external_strategy import choose_external_liquidity_target


SCENARIO = "LOW_IMPACT_CAUSAL_LIQUIDITY_CONTINUATION"


class LowImpactHybridTargetStrategy(LowImpactExternalLiquidityStrategy):
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.0
    DIRECT_MAX_BODY_ATR = 1.0
    MIN_TARGET_NET_R = 1.20

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
            "direct_max_delay_bars": self.DIRECT_MAX_DELAY_BARS,
            "direct_max_volume_ratio": self.DIRECT_MAX_VOLUME_RATIO,
            "direct_max_body_atr": self.DIRECT_MAX_BODY_ATR,
        }
        direct = (
            delay <= self.DIRECT_MAX_DELAY_BARS
            and volume_ratio <= self.DIRECT_MAX_VOLUME_RATIO
            and body <= self.DIRECT_MAX_BODY_ATR
        )
        if not direct:
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
        price_loss = side * (entry - stop)
        planned_loss = price_loss + cost_rate * (entry + stop)
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.excess_probe = None
            return

        external = choose_external_liquidity_target(
            self._external_levels(side),
            entry=entry,
            stop=stop,
            side=side,
            cost_rate=cost_rate,
            minimum_net_r=self.MIN_TARGET_NET_R,
        )
        if external is not None:
            target = external.price
            source = external.source
            reference_net_r = external.net_r
        else:
            range_width = probe.prior_high - probe.prior_low
            target = probe.sweep_extreme + side * range_width
            reference_net_r = net_r_at_price(
                entry,
                target,
                side,
                planned_loss,
                cost_rate,
            )
            source = "pre_event_30m_dealing_range_projection"
            if reference_net_r < self.MIN_TARGET_NET_R:
                self._event(
                    "NO_CAUSAL_LIQUIDITY_TARGET",
                    SCENARIO,
                    row,
                    {
                        **details,
                        "measured_target": target,
                        "measured_target_net_r": reference_net_r,
                        "minimum_target_net_r": self.MIN_TARGET_NET_R,
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
            target_reference=target,
            details=dict(details),
        )
        routed = {
            **details,
            "causal_target": target,
            "causal_target_source": source,
            "causal_target_net_r_at_confirmation": reference_net_r,
            "minimum_target_net_r": self.MIN_TARGET_NET_R,
        }
        self._event("LOW_IMPACT_CAUSAL_TARGET_CONFIRMED", SCENARIO, row, routed)
        submitted = LiquidityTransitionStrategy._submit_bracket(
            self,
            setup,
            row,
            self.MIN_TARGET_NET_R,
            routed,
        )
        if not submitted:
            self._event("CAUSAL_TARGET_EXECUTION_REJECTED", SCENARIO, row, routed)
        self.excess_probe = None


class HybridTargetStrictStrategy(LowImpactHybridTargetStrategy):
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.0
    DIRECT_MAX_BODY_ATR = 1.0


class HybridTargetNearEqualStrategy(LowImpactHybridTargetStrategy):
    DIRECT_MAX_DELAY_BARS = 3
    DIRECT_MAX_VOLUME_RATIO = 1.10
    DIRECT_MAX_BODY_ATR = 1.0


class HybridTargetTimelyStrategy(LowImpactHybridTargetStrategy):
    DIRECT_MAX_DELAY_BARS = 5
    DIRECT_MAX_VOLUME_RATIO = 1.10
    DIRECT_MAX_BODY_ATR = 1.0


class HybridTargetBalancedStrategy(LowImpactHybridTargetStrategy):
    DIRECT_MAX_DELAY_BARS = 5
    DIRECT_MAX_VOLUME_RATIO = 1.25
    DIRECT_MAX_BODY_ATR = 1.25


__all__ = [
    "HybridTargetBalancedStrategy",
    "HybridTargetNearEqualStrategy",
    "HybridTargetStrictStrategy",
    "HybridTargetTimelyStrategy",
    "LiquidityTransitionConfig",
]
