#!/usr/bin/env python3
"""Candidate 05 v56: resolve one spot-led pullback over a bounded same-leg watch.

v55 correctly separated spot-led information repricing from perpetual execution,
but treated the first internal-pool penetration bar as if interaction, defense
and entry confirmation had to finish simultaneously. Development evidence
showed hundreds of accepted contexts and first penetrations but zero entries.

v56 changes only that state transition. The first internal penetration freezes
one pool and one pullback extreme. The same causal leg receives at most three
completed response observations for reclaim, tail-flow recovery, visible depth,
trade-VWAP recovery and non-opposing spot flow. It never searches for a later
pool or resets the episode. All v46/v55 context, target, cost, risk, order and
NautilusTrader accounting contracts remain unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from logic import Pool
from spot_price_discovery_logic import SPOT_PULLBACK_PENETRATION_ATR_MIN
from spot_price_discovery_logic import spot_context_pullback_eligible
from spot_pullback_watch_logic import pullback_response_expired
from spot_pullback_watch_logic import spot_pullback_defense_ready
from spot_pullback_watch_logic import update_pullback_extreme
from strategy import LiquidityResponseConfig
from strategy_v55_spot_led_price_discovery import SpotLedPriceDiscoveryStrategy
from strategy_v55_spot_led_price_discovery import SpotPriceDiscoveryContext


@dataclass(slots=True)
class SpotPullbackDefenseWatch:
    context_scenario_id: str
    pool: Pool
    direction: int
    created_index: int
    created_ts: int
    extreme: float
    details: dict[str, Any]


class SpotPullbackWatchStrategy(SpotLedPriceDiscoveryStrategy):
    """Allow one penetrated internal level a bounded same-auction response."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.spot_pullback_watch: SpotPullbackDefenseWatch | None = None
        self.diagnostics.update(
            {
                "spot_pullback_watches": 0,
                "spot_pullback_watch_observations": 0,
                "spot_pullback_watch_confirmations": 0,
                "spot_pullback_watch_expiries": 0,
                "spot_pullback_watch_context_mismatches": 0,
                "spot_pullback_watch_feature_gaps": 0,
            },
        )

    def _close_spot_context(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        super()._close_spot_context(row, reason)
        self.spot_pullback_watch = None

    def _try_spot_led_pullback(
        self,
        row: dict[str, float | int],
    ) -> None:
        if self.spot_pullback_watch is not None:
            self._advance_spot_pullback_watch(row)
            return

        context = self.spot_context
        if context is None:
            return
        age = self.bar_index - context.created_index
        if not spot_context_pullback_eligible(
            accepted=context.accepted,
            age_bars=age,
        ):
            return
        if len(self.bars) < 2:
            return
        ts = int(row["ts"])
        if (
            not self._in_evaluation(ts)
            or self._funding_blackout(ts)
            or not self._features_ready(ts)
            or not self._spot_ready()
        ):
            return

        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        previous_close = float(list(self.bars)[-2]["close"])
        expected_kind = "LOW" if context.direction > 0 else "HIGH"
        crossed: list[Pool] = []
        for pool in self.spot_internal_pools.values():
            if pool.kind != expected_kind:
                continue
            if self.bar_index - pool.created_index < self.config.pool_min_age_bars:
                continue
            if context.direction > 0:
                is_crossed = (
                    previous_close >= pool.level
                    and float(row["low"])
                    <= pool.level - SPOT_PULLBACK_PENETRATION_ATR_MIN * atr
                )
            else:
                is_crossed = (
                    previous_close <= pool.level
                    and float(row["high"])
                    >= pool.level + SPOT_PULLBACK_PENETRATION_ATR_MIN * atr
                )
            if is_crossed:
                crossed.append(pool)
        if not crossed:
            return

        pool = (
            min(crossed, key=lambda item: (item.level, -item.strength))
            if context.direction > 0
            else max(crossed, key=lambda item: (item.level, item.strength))
        )
        for item in crossed:
            self.spot_internal_pools.pop(item.pool_id, None)
            self.diagnostics["spot_internal_pool_consumptions"] += 1
        self.diagnostics["spot_led_context_first_pullbacks"] += 1

        extreme = float(row["low"]) if context.direction > 0 else float(row["high"])
        details = {
            "pullback_watch_pool_id": pool.pool_id,
            "pullback_watch_pool_kind": pool.kind,
            "pullback_watch_pool_level": pool.level,
            "pullback_watch_pool_source": pool.source,
            "pullback_watch_pool_strength": pool.strength,
            "pullback_watch_created_index": self.bar_index,
            "pullback_watch_created_ts": ts,
            "pullback_watch_initial_extreme": extreme,
        }
        self.spot_pullback_watch = SpotPullbackDefenseWatch(
            context_scenario_id=context.scenario_id,
            pool=pool,
            direction=context.direction,
            created_index=self.bar_index,
            created_ts=ts,
            extreme=extreme,
            details=details,
        )
        self.diagnostics["spot_pullback_watches"] += 1
        self._transition(
            context.scenario_id,
            "SPOT_LED_PULLBACK_WATCH_ARMED",
            ts,
            ts,
            "AWAIT_PULLBACK_DEFENSE",
            "FIRST_INTERNAL_PENETRATION_FROZE_ONE_RESPONSE_EPISODE",
            pool.level,
            {**context.details, **details},
        )
        self._advance_spot_pullback_watch(row)

    def _advance_spot_pullback_watch(
        self,
        row: dict[str, float | int],
    ) -> None:
        watch = self.spot_pullback_watch
        context = self.spot_context
        if watch is None:
            return
        if context is None or context.scenario_id != watch.context_scenario_id:
            self.diagnostics["spot_pullback_watch_context_mismatches"] += 1
            self.spot_pullback_watch = None
            return

        age = self.bar_index - watch.created_index
        if pullback_response_expired(age_bars=age):
            self.diagnostics["spot_pullback_watch_expiries"] += 1
            self.diagnostics["spot_led_pullback_transfer_rejections"] += 1
            self._close_spot_context(
                row,
                "BOUNDED_SPOT_PULLBACK_DEFENSE_WINDOW_EXPIRED",
            )
            return

        watch.extreme = update_pullback_extreme(
            direction=watch.direction,
            current_extreme=watch.extreme,
            high=float(row["high"]),
            low=float(row["low"]),
        )
        self.diagnostics["spot_pullback_watch_observations"] += 1
        ts = int(row["ts"])
        if (
            not self._in_evaluation(ts)
            or self._funding_blackout(ts)
            or not self._features_ready(ts)
            or not self._spot_ready()
        ):
            self.diagnostics["spot_pullback_watch_feature_gaps"] += 1
            return

        ready = spot_pullback_defense_ready(
            direction=watch.direction,
            level=watch.pool.level,
            close=float(row["close"]),
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            depth_imbalance=self._feature("depth_imbalance_1"),
            trade_vwap=self._feature("trade_vwap_60s"),
            spot_flow_3m=self._feature("spot_flow_3m"),
        )
        if not ready:
            return
        if not self._spot_entry_slot_idle():
            self.diagnostics["spot_led_pullback_slot_conflicts"] += 1
            self._close_spot_context(
                row,
                "GLOBAL_ENTRY_SLOT_OCCUPIED_AT_CONFIRMED_SPOT_PULLBACK_DEFENSE",
            )
            return

        self.diagnostics["spot_pullback_watch_confirmations"] += 1
        response_bars = age + 1
        context.details.update(
            {
                **watch.details,
                "pullback_watch_response_bars": response_bars,
                "pullback_watch_episode_extreme": watch.extreme,
                "pullback_watch_confirmation_ts": ts,
                "pullback_watch_confirmation_close": float(row["close"]),
            },
        )
        execution_row = dict(row)
        if watch.direction > 0:
            execution_row["low"] = watch.extreme
        else:
            execution_row["high"] = watch.extreme
        submitted = super()._submit_spot_led_pullback(
            context,
            watch.pool,
            execution_row,
            self._atr(),
        )
        self.spot_pullback_watch = None
        if not submitted and self.spot_context is not None:
            self._close_spot_context(
                row,
                "CONFIRMED_SPOT_PULLBACK_WATCH_COULD_NOT_SUBMIT",
            )


LiquidityResponseStrategy = SpotPullbackWatchStrategy

__all__ = [
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "SpotPullbackDefenseWatch",
    "SpotPullbackWatchStrategy",
]
