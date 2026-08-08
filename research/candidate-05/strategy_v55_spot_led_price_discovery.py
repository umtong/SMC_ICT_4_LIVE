#!/usr/bin/env python3
"""Candidate 05 v55: spot-led repricing followed by one internal pullback.

v46 remains authoritative for failed-auction reversals in the perpetual market.
v55 adds a mutually distinct continuation family. A completed spot minute must
carry unusual, efficient, directional flow and move ahead of the perpetual.
The perpetual must then accept that repricing before the first causally observed
internal liquidity pullback can start a new continuation auction leg.

Context, state transition and execution use different evidence roles:

* spot trades define the information-repricing context;
* perpetual displacement accepts or invalidates the context;
* internal liquidity, current tail-flow recovery, visible depth and trade VWAP
  confirm the first pullback transfer;
* a price-capped marketable limit, structural pullback stop and still-live
  external liquidity target define execution geometry.

All orders, fills, fees, positions, margin and NAV remain owned by
NautilusTrader through the inherited v46 lifecycle.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import math
from typing import Any

from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import has_adverse_slippage_room
from logic import Pool
from logic import choose_liquidity_target
from logic import is_confirmed_pivot
from logic import net_r_at_price
from logic import planned_loss_per_unit
from retrace_logic import aggregate_completed_bar
from retrace_logic import structural_stop
from sponsored_choch_logic import slippage_protected_marketable_limit
from spot_price_discovery_logic import SPOT_CONTEXT_MAX_AGE_BARS
from spot_price_discovery_logic import SPOT_PULLBACK_PENETRATION_ATR_MIN
from spot_price_discovery_logic import spot_context_accepted
from spot_price_discovery_logic import spot_context_invalidated
from spot_price_discovery_logic import spot_context_pullback_eligible
from spot_price_discovery_logic import spot_led_direction
from spot_price_discovery_logic import spot_pullback_transfer_ready
from strategy import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


MINUTE_NS = 60_000_000_000
_BRANCH = "SPOT_LED_PRICE_DISCOVERY_PULLBACK"
_SPOT_POOL_MAX_AGE_BARS = 120


@dataclass(slots=True)
class SpotPriceDiscoveryContext:
    scenario_id: str
    direction: int
    created_index: int
    created_ts: int
    expires_index: int
    boundary_high: float
    boundary_low: float
    boundary_close: float
    atr: float
    favorable_extreme: float
    accepted: bool
    details: dict[str, Any]


class SpotLedPriceDiscoveryStrategy(NoPostRetraceBreakawayStrategy):
    """Route one first pullback after a completed spot-led repricing event."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.spot_context: SpotPriceDiscoveryContext | None = None
        self.spot_context_counter = 0
        self.spot_internal_pools: dict[str, Pool] = {}
        self.spot_pool_counter = 0
        self.spot_three_bars: deque[dict[str, float | int]] = deque(maxlen=1_500)
        self.spot_three_rows: list[dict[str, float | int]] = []
        self.spot_three_bucket: int | None = None
        self.diagnostics.update(
            {
                "spot_led_contexts": 0,
                "spot_led_context_opposite_replacements": 0,
                "spot_led_context_acceptances": 0,
                "spot_led_context_invalidations": 0,
                "spot_led_context_expiries": 0,
                "spot_led_context_first_pullbacks": 0,
                "spot_led_pullback_transfer_rejections": 0,
                "spot_led_pullback_slot_conflicts": 0,
                "spot_led_pullback_no_live_target": 0,
                "spot_led_pullback_geometry_rejections": 0,
                "spot_led_pullback_submissions": 0,
                "spot_internal_1m_pools": 0,
                "spot_internal_3m_pools": 0,
                "spot_internal_pool_strengthenings": 0,
                "spot_internal_pool_expirations": 0,
                "spot_internal_pool_consumptions": 0,
            },
        )

    def on_bar(self, bar: Any) -> None:
        # v46 receives the completed observation first and retains priority over
        # its validated failed-auction path. The new family may act only if the
        # inherited global entry/position slot remains idle afterwards.
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        self._update_spot_context(row)
        self._prune_spot_internal_pools(row)
        self._update_spot_internal_liquidity(row)
        self._try_spot_led_pullback(row)

    def _spot_ready(self) -> bool:
        feature = self.current_feature
        return bool(feature is not None and feature.get("spot_ready", False))

    def _update_spot_context(self, row: dict[str, float | int]) -> None:
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return

        context = self.spot_context
        if context is not None:
            if self.bar_index > context.expires_index:
                self.diagnostics["spot_led_context_expiries"] += 1
                self._close_spot_context(
                    row,
                    "SPOT_LED_PRICE_DISCOVERY_CONTEXT_EXPIRED",
                )
                context = None
            elif spot_context_invalidated(
                direction=context.direction,
                boundary_low=context.boundary_low,
                boundary_high=context.boundary_high,
                current_close=float(row["close"]),
                atr=context.atr,
            ):
                self.diagnostics["spot_led_context_invalidations"] += 1
                self._close_spot_context(
                    row,
                    "PERPETUAL_REJECTED_SPOT_LED_REPRICING",
                )
                context = None
            else:
                context.favorable_extreme = (
                    max(context.favorable_extreme, float(row["high"]))
                    if context.direction > 0
                    else min(context.favorable_extreme, float(row["low"]))
                )
                if not context.accepted and spot_context_accepted(
                    direction=context.direction,
                    boundary_close=context.boundary_close,
                    favorable_extreme=context.favorable_extreme,
                    atr=context.atr,
                ):
                    context.accepted = True
                    self.diagnostics["spot_led_context_acceptances"] += 1
                    self._transition(
                        context.scenario_id,
                        "SPOT_LED_REPRICING_ACCEPTED",
                        int(row["ts"]),
                        int(row["ts"]),
                        "AWAIT_FIRST_INTERNAL_PULLBACK",
                        "PERPETUAL_ACCEPTED_HALF_ATR_OF_SPOT_LED_PRICE_DISCOVERY",
                        float(row["close"]),
                        {
                            **context.details,
                            "acceptance_index": self.bar_index,
                            "acceptance_ts": int(row["ts"]),
                            "acceptance_close": float(row["close"]),
                            "favorable_displacement_atr": (
                                context.direction
                                * (context.favorable_extreme - context.boundary_close)
                                / context.atr
                            ),
                        },
                    )

        if not self._spot_ready():
            return
        direction = spot_led_direction(
            spot_ready=True,
            spot_flow_15s=self._feature("spot_flow_15s"),
            spot_flow_60s=self._feature("spot_flow_60s"),
            spot_notional_burst=self._feature("spot_notional_burst"),
            spot_ret_60s_bps=self._feature("spot_ret_60s_bps"),
            spot_efficiency_60s=self._feature("spot_efficiency_60s"),
            perp_minus_spot_return_bps=self._feature(
                "perp_minus_spot_return_bps",
            ),
        )
        if direction == 0:
            return
        current = self.spot_context
        if current is not None:
            if current.direction == direction:
                # A second burst in the same episode does not reset its clock or
                # create another independent opportunity.
                return
            self.diagnostics["spot_led_context_opposite_replacements"] += 1
            self._close_spot_context(
                row,
                "OPPOSITE_SPOT_INFORMATION_BURST_REPLACED_CONTEXT",
            )

        self.spot_context_counter += 1
        scenario_id = f"spot-pd-{self.spot_context_counter:07d}"
        details = {
            "branch": _BRANCH,
            "direction": direction,
            "spot_flow_15s": self._feature("spot_flow_15s"),
            "spot_flow_60s": self._feature("spot_flow_60s"),
            "spot_flow_3m": self._feature("spot_flow_3m"),
            "spot_notional_burst": self._feature("spot_notional_burst"),
            "spot_ret_60s_bps": self._feature("spot_ret_60s_bps"),
            "spot_efficiency_60s": self._feature("spot_efficiency_60s"),
            "perp_ret_60s_bps": self._feature("ret_60s_bps"),
            "perp_minus_spot_return_bps": self._feature(
                "perp_minus_spot_return_bps",
            ),
            "spot_lead_bps": -direction
            * self._feature("perp_minus_spot_return_bps"),
            "perp_spot_basis_bps": self._feature("perp_spot_basis_bps"),
            "created_index": self.bar_index,
            "created_ts": int(row["ts"]),
        }
        self.spot_context = SpotPriceDiscoveryContext(
            scenario_id=scenario_id,
            direction=direction,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            expires_index=self.bar_index + SPOT_CONTEXT_MAX_AGE_BARS,
            boundary_high=float(row["high"]),
            boundary_low=float(row["low"]),
            boundary_close=float(row["close"]),
            atr=atr,
            favorable_extreme=(
                float(row["high"]) if direction > 0 else float(row["low"])
            ),
            accepted=False,
            details=details,
        )
        self.diagnostics["spot_led_contexts"] += 1
        self._transition(
            scenario_id,
            "SPOT_LED_INFORMATION_BURST",
            int(row["ts"]),
            int(row["ts"]),
            "AWAIT_PERPETUAL_ACCEPTANCE",
            "SPOT_FLOW_NOTIONAL_EFFICIENCY_AND_PRICE_LED_PERPETUAL",
            float(row["close"]),
            details,
        )

    def _close_spot_context(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        context = self.spot_context
        if context is None:
            return
        if self.scenario_states.get(context.scenario_id) != "CLOSED":
            self._transition(
                context.scenario_id,
                "SPOT_LED_CONTEXT_CLOSED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                reason,
                float(row["close"]),
                context.details,
            )
        self.spot_context = None

    def _add_spot_internal_pool(
        self,
        kind: str,
        level: float,
        event_time_ns: int,
        observed_time_ns: int,
        source: str,
        *,
        strength: int,
    ) -> None:
        atr = self._atr()
        tolerance = (
            self.config.pool_merge_tolerance_atr * atr
            if math.isfinite(atr)
            else 0.0
        )
        merge: Pool | None = None
        for pool in self.spot_internal_pools.values():
            if pool.kind == kind and abs(pool.level - level) <= tolerance:
                merge = pool
                break
        if merge is not None:
            new_level = (
                max(merge.level, level)
                if kind == "HIGH"
                else min(merge.level, level)
            )
            self.spot_internal_pools[merge.pool_id] = replace(
                merge,
                level=new_level,
                observed_time_ns=observed_time_ns,
                strength=merge.strength + strength,
            )
            self.diagnostics["spot_internal_pool_strengthenings"] += 1
            return

        self.spot_pool_counter += 1
        pool_id = f"spot-pool-{self.spot_pool_counter:07d}"
        self.spot_internal_pools[pool_id] = Pool(
            pool_id=pool_id,
            kind=kind,
            level=level,
            event_time_ns=event_time_ns,
            observed_time_ns=observed_time_ns,
            source=source,
            strength=strength,
            created_index=self.bar_index,
        )

    def _update_spot_internal_liquidity(
        self,
        row: dict[str, float | int],
    ) -> None:
        self._confirm_spot_one_minute_pivot(int(row["ts"]))
        self._update_spot_three_minute(row)

    def _confirm_spot_one_minute_pivot(self, observed_ns: int) -> None:
        span = 2
        rows = list(self.bars)
        if len(rows) < 2 * span + 1:
            return
        window = rows[-(2 * span + 1) :]
        center = window[span]
        highs = [float(item["high"]) for item in window]
        lows = [float(item["low"]) for item in window]
        if is_confirmed_pivot(highs, span=span, kind="HIGH"):
            self._add_spot_internal_pool(
                "HIGH",
                float(center["high"]),
                int(center["ts"]),
                observed_ns,
                "SPOT_CONTEXT_CONFIRMED_1M_INTERNAL",
                strength=1,
            )
            self.diagnostics["spot_internal_1m_pools"] += 1
        if is_confirmed_pivot(lows, span=span, kind="LOW"):
            self._add_spot_internal_pool(
                "LOW",
                float(center["low"]),
                int(center["ts"]),
                observed_ns,
                "SPOT_CONTEXT_CONFIRMED_1M_INTERNAL",
                strength=1,
            )
            self.diagnostics["spot_internal_1m_pools"] += 1

    def _update_spot_three_minute(
        self,
        row: dict[str, float | int],
    ) -> None:
        minute = int(row["ts"]) // MINUTE_NS
        bucket = minute // 3
        if self.spot_three_bucket is None:
            self.spot_three_bucket = bucket
        elif bucket != self.spot_three_bucket:
            self.spot_three_rows = []
            self.spot_three_bucket = bucket
        self.spot_three_rows.append(row.copy())
        if minute % 3 != 2:
            return
        if len(self.spot_three_rows) == 3:
            self.spot_three_bars.append(
                aggregate_completed_bar(self.spot_three_rows),
            )
            self._confirm_spot_three_minute_pivot(int(row["ts"]))
        self.spot_three_rows = []
        self.spot_three_bucket = None

    def _confirm_spot_three_minute_pivot(self, observed_ns: int) -> None:
        span = 2
        rows = list(self.spot_three_bars)
        if len(rows) < 2 * span + 1:
            return
        window = rows[-(2 * span + 1) :]
        center = window[span]
        highs = [float(item["high"]) for item in window]
        lows = [float(item["low"]) for item in window]
        if is_confirmed_pivot(highs, span=span, kind="HIGH"):
            self._add_spot_internal_pool(
                "HIGH",
                float(center["high"]),
                int(center["ts"]),
                observed_ns,
                "SPOT_CONTEXT_CONFIRMED_3M_INTERNAL",
                strength=2,
            )
            self.diagnostics["spot_internal_3m_pools"] += 1
        if is_confirmed_pivot(lows, span=span, kind="LOW"):
            self._add_spot_internal_pool(
                "LOW",
                float(center["low"]),
                int(center["ts"]),
                observed_ns,
                "SPOT_CONTEXT_CONFIRMED_3M_INTERNAL",
                strength=2,
            )
            self.diagnostics["spot_internal_3m_pools"] += 1

    def _prune_spot_internal_pools(
        self,
        row: dict[str, float | int],
    ) -> None:
        expired = [
            pool
            for pool in self.spot_internal_pools.values()
            if self.bar_index - pool.created_index > _SPOT_POOL_MAX_AGE_BARS
        ]
        for pool in expired:
            self.spot_internal_pools.pop(pool.pool_id, None)
            self.diagnostics["spot_internal_pool_expirations"] += 1

    def _spot_entry_slot_idle(self) -> bool:
        return (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and not bool(getattr(self, "exit_pending", False))
            and self.pending is None
            and self.armed_entry_path is None
            and not bool(getattr(self, "entry_cancel_pending", False))
            and not bool(getattr(self, "counter_context_parent_lock_active", False))
        )

    def _try_spot_led_pullback(
        self,
        row: dict[str, float | int],
    ) -> None:
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

        ready = spot_pullback_transfer_ready(
            direction=context.direction,
            pool_kind=pool.kind,
            pool_level=pool.level,
            previous_close=previous_close,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            atr=atr,
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            depth_imbalance=self._feature("depth_imbalance_1"),
            trade_vwap=self._feature("trade_vwap_60s"),
            spot_flow_3m=self._feature("spot_flow_3m"),
        )
        if not ready:
            self.diagnostics["spot_led_pullback_transfer_rejections"] += 1
            self._close_spot_context(
                row,
                "FIRST_INTERNAL_PULLBACK_LACKED_CURRENT_TRANSFER_EVIDENCE",
            )
            return
        if not self._spot_entry_slot_idle():
            self.diagnostics["spot_led_pullback_slot_conflicts"] += 1
            self._close_spot_context(
                row,
                "GLOBAL_ENTRY_SLOT_OCCUPIED_AT_SPOT_LED_PULLBACK",
            )
            return
        self._submit_spot_led_pullback(context, pool, row, atr)

    def _submit_spot_led_pullback(
        self,
        context: SpotPriceDiscoveryContext,
        pool: Pool,
        row: dict[str, float | int],
        atr: float,
    ) -> bool:
        side = context.direction
        observed = float(row["close"])
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        raw_entry = slippage_protected_marketable_limit(
            observed_price=observed,
            side=side,
            adverse_slippage_rate=slippage_rate,
            price_increment=_as_float(self.instrument.price_increment),
        )
        entry_price = self.instrument.make_price(raw_entry)
        entry = _as_float(entry_price)
        if not has_adverse_slippage_room(
            observed_price=observed,
            limit_price=entry,
            side=side,
            adverse_slippage_rate=slippage_rate,
        ):
            self.diagnostics["spot_led_pullback_geometry_rejections"] += 1
            self._close_spot_context(
                row,
                "SPOT_PULLBACK_LIMIT_LACKED_ADVERSE_SLIPPAGE_ROOM",
            )
            return False

        sweep_extreme = (
            float(row["low"]) if side > 0 else float(row["high"])
        )
        raw_stop = structural_stop(
            sweep_extreme,
            side,
            atr,
            self.config.stop_buffer_atr,
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["spot_led_pullback_geometry_rejections"] += 1
            self._close_spot_context(
                row,
                "SPOT_PULLBACK_PLANNED_LOSS_GEOMETRY_INVALID",
            )
            return False

        target, target_source, target_r = choose_liquidity_target(
            entry=entry,
            side=side,
            pools=list(self.active_pools.values()),
            planned_loss=planned_loss,
            cost_rate=cost_rate,
            min_net_r=MIN_LIQUIDITY_TARGET_NET_R,
            max_net_r=MAX_LIQUIDITY_TARGET_NET_R,
            fallback_net_r=self.config.acceptance_target_net_r,
        )
        if not target_source.startswith("POOL:"):
            self.diagnostics["spot_led_pullback_no_live_target"] += 1
            self._close_spot_context(
                row,
                "NO_STILL_LIVE_EXTERNAL_LIQUIDITY_TARGET_FOR_SPOT_PULLBACK",
            )
            return False
        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        rounded_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if (
            rounded_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R
            or (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["spot_led_pullback_geometry_rejections"] += 1
            self._close_spot_context(
                row,
                "SPOT_PULLBACK_ROUNDED_BRACKET_INVALID",
            )
            return False

        details = {
            **context.details,
            "accepted": context.accepted,
            "context_age_bars": self.bar_index - context.created_index,
            "internal_pool_id": pool.pool_id,
            "internal_pool_kind": pool.kind,
            "internal_pool_level": pool.level,
            "internal_pool_source": pool.source,
            "internal_pool_strength": pool.strength,
            "pullback_ts": int(row["ts"]),
            "pullback_high": float(row["high"]),
            "pullback_low": float(row["low"]),
            "pullback_close": observed,
            "pullback_flow_15s": self._feature("flow_15s"),
            "pullback_flow_60s": self._feature("flow_60s"),
            "pullback_depth_imbalance_1": self._feature("depth_imbalance_1"),
            "pullback_trade_vwap_60s": self._feature("trade_vwap_60s"),
            "pullback_spot_flow_3m": self._feature("spot_flow_3m"),
            "entry_limit": entry,
            "stop": stop,
            "target": target,
            "target_source": target_source,
            "target_net_r": rounded_r,
        }
        setup = PendingSetup(
            scenario_id=context.scenario_id,
            branch=_BRANCH,
            side=side,
            swept_kind=pool.kind,
            pool_id=pool.pool_id,
            pool_level=pool.level,
            created_index=self.bar_index,
            expires_index=self.bar_index + 2,
            sweep_extreme=sweep_extreme,
            structure=pool.level,
            atr=atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="SPOT_LED_FIRST_INTERNAL_PULLBACK",
            choch_close=observed,
            stop=stop,
            atr=atr,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            details=details,
        )
        self.armed_entry_path = armed
        submitted = self._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=target_r,
            branch=_BRANCH,
            event_type="SPOT_LED_PULLBACK_LIMIT_SUBMITTED",
            reason="FIRST_INTERNAL_LIQUIDITY_TRANSFER_AFTER_SPOT_LED_ACCEPTANCE",
            expires_index=self.bar_index + 2,
            entry_tag="SPOT_LED_PRICE_DISCOVERY_ENTRY",
            extra=details,
        )
        if submitted:
            self.diagnostics["spot_led_pullback_submissions"] += 1
            self.spot_context = None
            return True
        self._close_spot_context(
            row,
            "SPOT_LED_PULLBACK_BRACKET_SUBMISSION_FAILED",
        )
        return False


LiquidityResponseStrategy = SpotLedPriceDiscoveryStrategy

__all__ = [
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "SpotLedPriceDiscoveryStrategy",
    "SpotPriceDiscoveryContext",
]
