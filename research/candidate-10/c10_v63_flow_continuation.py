"""Independent flow-shock continuation scenario for Candidate 10.

This is not another source-sweep FAR filter.  It implements a separate accepted
price-discovery family from reusable Candidate 11 infrastructure:

completed 5-minute liquidity structure
    -> close breakout with directional aggressor flow and displacement
    -> cross-market event-direction leadership
    -> passive retrace to the first execution void
    -> invalidation behind the last opposite 5-minute pivot
    -> nearest still-live, pre-existing 4-hour/day liquidity objective

The detector, entry, invalidation and target all belong to the new breakout leg.
No fixed profit target, return threshold, fitted score, session whitelist or
symbol specialization is introduced.  NautilusTrader remains the sole owner of
orders, fills, fees, margin, positions and account NAV.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from logic import (
    MINUTE_NS,
    BarObs,
    CausalAuctionEngine,
    Direction,
    Pool,
    Scenario,
    Side,
    TradePlan,
)


class FlowShockContinuationEngine(CausalAuctionEngine):
    """Causal true-acceptance continuation on one instrument."""

    def __init__(self, config: Any, instrument_id: str) -> None:
        super().__init__(config, instrument_id)
        self.pending_plan_id: str | None = None
        self.pending_target_id: str | None = None
        self.used_breakouts: set[tuple[str, int]] = set()
        self.used_target_ids: set[str] = set()

    @staticmethod
    def _latest_before(
        points: list[tuple[int, int, float]],
        before_ts_ns: int,
        predicate: Any | None = None,
    ) -> tuple[int, int, float] | None:
        eligible = [
            point
            for point in points
            if point[1] < before_ts_ns
            and (predicate is None or bool(predicate(point[2])))
        ]
        return eligible[-1] if eligible else None

    def _consume_external_first_passage(
        self,
        bar: BarObs,
        prev: BarObs,
    ) -> None:
        """Retire completed higher-timeframe liquidity after causal passage."""

        for pool in self.pools:
            if (
                pool.consumed
                or not pool.external
                or pool.confirmed_index >= self._index
            ):
                continue
            crossed = (
                prev.close <= pool.level <= bar.high
                if pool.side == Side.HIGH
                else prev.close >= pool.level >= bar.low
            )
            if not crossed:
                continue
            pool.consumed = True
            self._event(
                pool.scenario_id,
                "EXTERNAL_LIQUIDITY_CONSUMED",
                bar.ts_ns,
                bar.ts_ns,
                "ARMED",
                "TERMINAL",
                "CAUSAL_FIRST_PASSAGE",
                pool.level,
                {
                    "side": pool.side.value,
                    "source": pool.source,
                    "consumer": "FLOW_SHOCK_CONTINUATION_MAP",
                },
            )

    def _nearest_target(
        self,
        direction: Direction,
        bar: BarObs,
    ) -> Pool | None:
        if direction == Direction.LONG:
            candidates = [
                pool
                for pool in self.pools
                if not pool.consumed
                and pool.external
                and pool.scenario_id not in self.used_target_ids
                and pool.confirmed_index < self._index
                and pool.side == Side.HIGH
                and pool.level > bar.high
            ]
            return min(candidates, key=lambda pool: pool.level, default=None)
        candidates = [
            pool
            for pool in self.pools
            if not pool.consumed
            and pool.external
            and pool.scenario_id not in self.used_target_ids
            and pool.confirmed_index < self._index
            and pool.side == Side.LOW
            and pool.level < bar.low
        ]
        return max(candidates, key=lambda pool: pool.level, default=None)

    def _build_plan(
        self,
        *,
        bar: BarObs,
        prev: BarObs,
        atr: float,
        direction: Direction,
        breakout: tuple[int, int, float],
        stop_pivot: tuple[int, int, float],
        target_pool: Pool,
        relative_volume: float,
    ) -> TradePlan | None:
        key = (direction.value, breakout[0])
        if key in self.used_breakouts:
            self.skips["FLOW_BREAKOUT_ALREADY_USED"] += 1
            return None
        zone_low, zone_high = self._zone_from_displacement(
            self.bars,
            self._index,
            direction,
        )
        entry = zone_high if direction == Direction.LONG else zone_low
        stop = (
            stop_pivot[2] - self.config.stop_buffer_atr * atr
            if direction == Direction.LONG
            else stop_pivot[2] + self.config.stop_buffer_atr * atr
        )
        target = target_pool.level
        if direction == Direction.LONG:
            risk = entry - stop
            reward = target - entry
            passive = entry < bar.close
        else:
            risk = stop - entry
            reward = entry - target
            passive = entry > bar.close
        if not passive:
            self.skips["FLOW_LIMIT_NOT_PASSIVE"] += 1
            return None
        if risk <= 0.0 or reward <= 0.0:
            self.skips["FLOW_NON_CAUSAL_PRICE_ORDER"] += 1
            return None
        if risk / atr < self.config.min_stop_atr:
            self.skips["FLOW_STOP_DISTANCE_BELOW_EXECUTION_FLOOR"] += 1
            return None

        maker = self.config.effective_maker_rate
        taker = self.config.effective_taker_rate
        loss = risk + entry * maker + stop * taker
        gain = reward - entry * maker - target * maker
        net_r = gain / loss if loss > 0.0 else float("-inf")
        if gain <= 0.0 or net_r < self.config.min_net_r:
            self.skips["FLOW_INSUFFICIENT_COSTED_STRUCTURAL_R"] += 1
            return None

        scenario_id = (
            f"{self.instrument_id}-FLOW-{direction.value}-"
            f"{breakout[0]}-{bar.ts_ns}-{target_pool.scenario_id}"
        )
        expire_ts_ns = bar.ts_ns + self.config.retrace_expiry_bars * MINUTE_NS
        plan = TradePlan(
            scenario_id=scenario_id,
            scenario=Scenario.AAC,
            direction=direction,
            observed_ts_ns=bar.ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            atr=atr,
            loss_per_unit=loss,
            gain_per_unit=gain,
            net_r=net_r,
            reason_code="FLOW_SHOCK_TRUE_ACCEPTANCE_CONTINUATION",
            expire_ts_ns=expire_ts_ns,
            entry_order_type="LIMIT",
            entry_post_only=True,
            details={
                "sweep_ts_ns": prev.ts_ns,
                "impulse_start_ts_ns": prev.ts_ns,
                "breakout_pivot_event_ts_ns": breakout[0],
                "breakout_pivot_known_ts_ns": breakout[1],
                "breakout_level": breakout[2],
                "stop_pivot_event_ts_ns": stop_pivot[0],
                "stop_pivot_known_ts_ns": stop_pivot[1],
                "stop_pivot_level": stop_pivot[2],
                "target_pool_id": target_pool.scenario_id,
                "target_pool_source": target_pool.source,
                "target_pool_confirmed_ts_ns": target_pool.confirmed_ts_ns,
                "target_pool_level": target_pool.level,
                "zone_low": zone_low,
                "zone_high": zone_high,
                "confirmation_close": bar.close,
                "confirmation_body_atr": bar.body / atr,
                "confirmation_signed_flow": bar.signed_flow,
                "confirmation_close_location": bar.close_location,
                "relative_volume": relative_volume,
                "draw_method": "PREEXISTING_HIGHER_TIMEFRAME_LIQUIDITY",
                "entry_cost_assumption": "MAKER",
                "entry_expiry_bars": self.config.retrace_expiry_bars,
                "state_sequence": [
                    "COMPLETED_INTERNAL_LIQUIDITY",
                    "FLOW_SHOCK_BREAKOUT_CONFIRMED",
                    "CROSS_MARKET_EVENT_LEADER_PENDING",
                    "FIRST_EXECUTION_VOID_RETRACE_PENDING",
                    "POSITION_OR_ENTRY_TERMINAL",
                ],
                "new_fitted_thresholds": [],
            },
        )
        self.used_breakouts.add(key)
        self.pending_plan_id = scenario_id
        self.pending_target_id = target_pool.scenario_id
        self._event(
            scenario_id,
            "FLOW_SHOCK_CONTINUATION_CONFIRMED",
            prev.ts_ns,
            bar.ts_ns,
            "OBSERVE",
            "AAC_CONFIRMED",
            "INTERNAL_BREAKOUT_DISPLACEMENT_AGGRESSOR_FLOW",
            breakout[2],
            {
                "direction": direction.value,
                "target": target,
                "target_pool": target_pool.scenario_id,
                "entry": entry,
                "stop": stop,
                "net_r": net_r,
            },
        )
        self._event(
            scenario_id,
            "TRADE_PLAN_CONFIRMED",
            prev.ts_ns,
            bar.ts_ns,
            "AAC_CONFIRMED",
            "PENDING_ENTRY",
            plan.reason_code,
            entry,
            {
                "scenario": Scenario.AAC.value,
                "direction": direction.value,
                "target": target,
                "stop": stop,
                "expire_ts_ns": expire_ts_ns,
                "net_r": net_r,
            },
        )
        return plan

    def _detect_flow_plan(
        self,
        bar: BarObs,
        prev: BarObs,
        atr: float,
        relative_volume: float,
    ) -> TradePlan | None:
        high = self._latest_before(self.internal_highs, bar.ts_ns)
        low = self._latest_before(self.internal_lows, bar.ts_ns)
        if high is None or low is None:
            self.skips["FLOW_CAUSAL_INTERNAL_STRUCTURE_UNAVAILABLE"] += 1
            return None
        body_ok = bar.body >= self.config.reacceleration_body_atr * atr
        volume_ok = relative_volume >= self.config.min_relative_volume
        if not body_ok or not volume_ok:
            return None

        long_breakout = (
            prev.close <= high[2] < bar.close
            and bar.signed_flow >= self.config.reacceleration_flow_min
            and bar.close_location >= 0.60
        )
        short_breakout = (
            prev.close >= low[2] > bar.close
            and bar.signed_flow <= -self.config.reacceleration_flow_min
            and bar.close_location <= 0.40
        )
        if long_breakout and short_breakout:
            self.skips["FLOW_AMBIGUOUS_BOTH_DIRECTIONS"] += 1
            return None
        if not long_breakout and not short_breakout:
            return None

        direction = Direction.LONG if long_breakout else Direction.SHORT
        breakout = high if long_breakout else low
        target = self._nearest_target(direction, bar)
        if target is None:
            self.skips["FLOW_NO_LIVE_HIGHER_TIMEFRAME_TARGET"] += 1
            return None
        entry_probe = (
            self._zone_from_displacement(self.bars, self._index, direction)[1]
            if direction == Direction.LONG
            else self._zone_from_displacement(self.bars, self._index, direction)[0]
        )
        stop_pivot = (
            self._latest_before(
                self.internal_lows,
                bar.ts_ns,
                lambda level: level < entry_probe,
            )
            if direction == Direction.LONG
            else self._latest_before(
                self.internal_highs,
                bar.ts_ns,
                lambda level: level > entry_probe,
            )
        )
        if stop_pivot is None:
            self.skips["FLOW_SAME_LEG_INVALIDATION_UNAVAILABLE"] += 1
            return None
        return self._build_plan(
            bar=bar,
            prev=prev,
            atr=atr,
            direction=direction,
            breakout=breakout,
            stop_pivot=stop_pivot,
            target_pool=target,
            relative_volume=relative_volume,
        )

    def on_bar(self, bar: BarObs, *, allow_entry: bool = True) -> TradePlan | None:
        self._index += 1
        prev = self.bars[-1] if self.bars else None
        true_range = (
            bar.high - bar.low
            if prev is None
            else max(
                bar.high - bar.low,
                abs(bar.high - prev.close),
                abs(bar.low - prev.close),
            )
        )
        self.true_ranges.append(true_range)
        self.volumes.append(bar.volume)
        self.bars.append(bar)
        self._update_structure(bar)
        atr = self.atr
        median_volume = self.median_volume
        if atr is None or median_volume is None or atr <= 0.0 or prev is None:
            return None
        self._expire_pools(bar.ts_ns)
        self._consume_external_first_passage(bar, prev)
        if self.active_trade_id is not None or self.pending_plan_id is not None:
            return None
        plan = self._detect_flow_plan(
            bar,
            prev,
            atr,
            bar.volume / max(median_volume, 1e-12),
        )
        if plan is not None and not allow_entry:
            self.mark_rejected(
                plan,
                bar.ts_ns,
                "OUTSIDE_EVALUATION_WINDOW",
            )
            return None
        return plan

    def mark_rejected(
        self,
        plan: TradePlan,
        ts_ns: int,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.pending_plan_id != plan.scenario_id:
            return
        self._event(
            plan.scenario_id,
            "ENTRY_PLAN_REJECTED",
            plan.observed_ts_ns,
            ts_ns,
            "PENDING_ENTRY",
            "TERMINAL",
            reason,
            plan.expected_entry,
            details or {},
        )
        self.skips[reason] += 1
        self.pending_plan_id = None
        self.pending_target_id = None

    def mark_submitted(
        self,
        plan: TradePlan,
        quantity: Decimal,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.active_trade_id is not None:
            raise RuntimeError("flow continuation already owns active trade")
        if self.pending_plan_id != plan.scenario_id:
            raise RuntimeError("submitted flow plan does not match pending plan")
        self._event(
            plan.scenario_id,
            "ENTRY_ORDER_LIST_SUBMITTED",
            plan.observed_ts_ns,
            plan.observed_ts_ns,
            "PENDING_ENTRY",
            "PENDING_ENTRY",
            plan.reason_code,
            plan.expected_entry,
            {
                "scenario": plan.scenario.value,
                "direction": plan.direction.value,
                "quantity": str(quantity),
                "net_r": plan.net_r,
                **(details or {}),
            },
        )
        if self.pending_target_id is not None:
            self.used_target_ids.add(self.pending_target_id)
        self.active_trade_id = plan.scenario_id
        self.active_trade_state = "PENDING_ENTRY"
        self.pending_plan_id = None
        self.pending_target_id = None


__all__ = ["FlowShockContinuationEngine"]
