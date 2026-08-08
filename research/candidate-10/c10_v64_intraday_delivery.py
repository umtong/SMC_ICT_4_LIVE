"""Intraday same-leg accepted-auction delivery for Candidate 10.

This family preserves v63's useful causal observations but removes the two
structural causes of its apparent H1 success and July collapse:

* the leadership event now begins when the broken five-minute pivot became
  known, rather than one minute before confirmation;
* entry, invalidation and objective all belong to the breakout leg, and any
  surviving position expires after one four-hour auction horizon.

Scenario:

completed five-minute pivot and completed four-hour context
    -> displacement/flow close through the pivot
    -> passive retest of the broken level / first execution void
    -> invalidation beyond the signal-bar and acceptance boundary
    -> nearest still-live causally confirmed five-minute pivot
    -> target, invalidation, or one-context-horizon expiry

The engine does not specialize by symbol, fit a return threshold, use future
bars, or alter the fixed three-percent current-NAV risk contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from c10_v63_flow_continuation import FlowShockContinuationEngine
from logic import MINUTE_NS, BarObs, Direction, Scenario, Side, TradePlan


@dataclass(frozen=True, slots=True)
class InternalLiquidityTarget:
    scenario_id: str
    side: Side
    level: float
    source: str
    event_ts_ns: int
    confirmed_ts_ns: int


class IntradayDeliveryContinuationEngine(FlowShockContinuationEngine):
    """Accepted-auction continuation bounded to one causal intraday leg."""

    POSITION_HORIZON_NS = 240 * MINUTE_NS

    def __init__(self, config: Any, instrument_id: str) -> None:
        super().__init__(config, instrument_id)
        self.consumed_internal_target_ids: set[str] = set()

    def _internal_target_id(
        self,
        side: Side,
        point: tuple[int, int, float],
    ) -> str:
        event_ts_ns, known_ts_ns, _ = point
        return (
            f"{self.instrument_id}-INTERNAL-{side.value}-"
            f"{event_ts_ns}-{known_ts_ns}"
        )

    def _consume_internal_first_passage(
        self,
        bar: BarObs,
    ) -> None:
        """Retire a five-minute pivot after its first post-confirmation touch."""

        for side, points in (
            (Side.HIGH, self.internal_highs),
            (Side.LOW, self.internal_lows),
        ):
            for point in points:
                _, known_ts_ns, level = point
                target_id = self._internal_target_id(side, point)
                if (
                    known_ts_ns >= bar.ts_ns
                    or target_id in self.consumed_internal_target_ids
                ):
                    continue
                touched = bar.high >= level if side == Side.HIGH else bar.low <= level
                if touched:
                    self.consumed_internal_target_ids.add(target_id)

    def _consume_external_first_passage(
        self,
        bar: BarObs,
        prev: BarObs,
    ) -> None:
        super()._consume_external_first_passage(bar, prev)
        self._consume_internal_first_passage(bar)

    def _nearest_internal_target(
        self,
        direction: Direction,
        bar: BarObs,
    ) -> InternalLiquidityTarget | None:
        side = Side.HIGH if direction == Direction.LONG else Side.LOW
        points = self.internal_highs if side == Side.HIGH else self.internal_lows
        candidates: list[InternalLiquidityTarget] = []
        for point in points:
            event_ts_ns, known_ts_ns, level = point
            target_id = self._internal_target_id(side, point)
            if (
                known_ts_ns >= bar.ts_ns
                or target_id in self.consumed_internal_target_ids
                or target_id in self.used_target_ids
            ):
                continue
            live = level > bar.high if side == Side.HIGH else level < bar.low
            if not live:
                continue
            candidates.append(
                InternalLiquidityTarget(
                    scenario_id=target_id,
                    side=side,
                    level=level,
                    source="CONFIRMED_5M_INTERNAL_LIQUIDITY",
                    event_ts_ns=event_ts_ns,
                    confirmed_ts_ns=known_ts_ns,
                ),
            )
        if direction == Direction.LONG:
            return min(candidates, key=lambda target: target.level, default=None)
        return max(candidates, key=lambda target: target.level, default=None)

    def _completed_context_state(self, direction: Direction) -> dict[str, Any]:
        """Classify the last completed 4H auction without using the signal bar."""

        if len(self.context_bars) < 2:
            return {
                "state": "UNRESOLVED_CONTEXT",
                "aligned": False,
                "reason": "TWO_COMPLETED_4H_AUCTIONS_UNAVAILABLE",
            }
        previous, latest = self.context_bars[-2:]
        latest_mid = (latest.high + latest.low) / 2.0
        if direction == Direction.LONG:
            price_aligned = latest.close > latest.open and latest.close > previous.close
            location_aligned = latest.close >= latest_mid
            flow_aligned = latest.signed_flow >= 0.0
            state = "BULLISH_4H_ACCEPTANCE"
        else:
            price_aligned = latest.close < latest.open and latest.close < previous.close
            location_aligned = latest.close <= latest_mid
            flow_aligned = latest.signed_flow <= 0.0
            state = "BEARISH_4H_ACCEPTANCE"
        aligned = bool(price_aligned and location_aligned and flow_aligned)
        return {
            "state": state if aligned else "UNRESOLVED_CONTEXT",
            "aligned": aligned,
            "reason": (
                "COMPLETED_4H_PRICE_LOCATION_AND_FLOW_ALIGNED"
                if aligned
                else "COMPLETED_4H_ACCEPTANCE_NOT_ALIGNED"
            ),
            "direction": direction.value,
            "previous_start_ts_ns": previous.start_ts_ns,
            "previous_end_ts_ns": previous.end_ts_ns,
            "previous_close": previous.close,
            "latest_start_ts_ns": latest.start_ts_ns,
            "latest_end_ts_ns": latest.end_ts_ns,
            "latest_open": latest.open,
            "latest_high": latest.high,
            "latest_low": latest.low,
            "latest_close": latest.close,
            "latest_signed_flow": latest.signed_flow,
            "price_aligned": price_aligned,
            "location_aligned": location_aligned,
            "flow_aligned": flow_aligned,
        }

    def _build_plan(
        self,
        *,
        bar: BarObs,
        prev: BarObs,
        atr: float,
        direction: Direction,
        breakout: tuple[int, int, float],
        target: InternalLiquidityTarget,
        relative_volume: float,
        context: dict[str, Any],
    ) -> TradePlan | None:
        key = (direction.value, breakout[0])
        if key in self.used_breakouts:
            self.skips["INTRADAY_BREAKOUT_ALREADY_USED"] += 1
            return None

        zone_low, zone_high = self._zone_from_displacement(
            self.bars,
            self._index,
            direction,
        )
        breakout_level = breakout[2]
        if direction == Direction.LONG:
            entry = max(zone_high, breakout_level)
            stop_anchor = min(zone_low, breakout_level, bar.low)
            stop = stop_anchor - self.config.stop_buffer_atr * atr
            reward = target.level - entry
            risk = entry - stop
            passive = entry < bar.close
        else:
            entry = min(zone_low, breakout_level)
            stop_anchor = max(zone_high, breakout_level, bar.high)
            stop = stop_anchor + self.config.stop_buffer_atr * atr
            reward = entry - target.level
            risk = stop - entry
            passive = entry > bar.close

        if not passive:
            self.skips["INTRADAY_RETEST_LIMIT_NOT_PASSIVE"] += 1
            return None
        if risk <= 0.0 or reward <= 0.0:
            self.skips["INTRADAY_NON_CAUSAL_PRICE_ORDER"] += 1
            return None
        if risk / atr < self.config.min_stop_atr:
            self.skips["INTRADAY_STOP_DISTANCE_BELOW_EXECUTION_FLOOR"] += 1
            return None

        maker = self.config.effective_maker_rate
        taker = self.config.effective_taker_rate
        loss = risk + entry * maker + stop * taker
        gain = reward - entry * maker - target.level * maker
        net_r = gain / loss if loss > 0.0 else float("-inf")
        if gain <= 0.0 or net_r < self.config.min_net_r:
            self.skips["INTRADAY_INSUFFICIENT_COSTED_STRUCTURAL_R"] += 1
            return None

        impulse_start_ts_ns = breakout[1]
        if impulse_start_ts_ns >= bar.ts_ns:
            self.skips["INTRADAY_INVALID_IMPULSE_INTERVAL"] += 1
            return None
        scenario_id = (
            f"{self.instrument_id}-INTRADAY-{direction.value}-"
            f"{breakout[0]}-{bar.ts_ns}-{target.scenario_id}"
        )
        entry_expire_ts_ns = (
            bar.ts_ns + self.config.retrace_expiry_bars * MINUTE_NS
        )
        position_expire_ts_ns = bar.ts_ns + self.POSITION_HORIZON_NS
        plan = TradePlan(
            scenario_id=scenario_id,
            scenario=Scenario.AAC,
            direction=direction,
            observed_ts_ns=bar.ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target.level,
            atr=atr,
            loss_per_unit=loss,
            gain_per_unit=gain,
            net_r=net_r,
            reason_code="INTRADAY_SAME_LEG_ACCEPTED_AUCTION_DELIVERY",
            expire_ts_ns=entry_expire_ts_ns,
            entry_order_type="LIMIT",
            entry_post_only=True,
            details={
                "sweep_ts_ns": impulse_start_ts_ns,
                "impulse_start_ts_ns": impulse_start_ts_ns,
                "breakout_pivot_event_ts_ns": breakout[0],
                "breakout_pivot_known_ts_ns": breakout[1],
                "breakout_level": breakout_level,
                "target_pool_id": target.scenario_id,
                "target_pool_source": target.source,
                "target_pool_event_ts_ns": target.event_ts_ns,
                "target_pool_confirmed_ts_ns": target.confirmed_ts_ns,
                "target_pool_level": target.level,
                "zone_low": zone_low,
                "zone_high": zone_high,
                "acceptance_boundary": breakout_level,
                "invalidation_anchor": stop_anchor,
                "confirmation_open": bar.open,
                "confirmation_high": bar.high,
                "confirmation_low": bar.low,
                "confirmation_close": bar.close,
                "confirmation_body_atr": bar.body / atr,
                "confirmation_signed_flow": bar.signed_flow,
                "confirmation_close_location": bar.close_location,
                "relative_volume": relative_volume,
                "completed_4h_context": context,
                "position_expire_ts_ns": position_expire_ts_ns,
                "position_horizon_minutes": 240,
                "draw_method": "NEAREST_LIVE_CONFIRMED_5M_INTERNAL_LIQUIDITY",
                "entry_cost_assumption": "MAKER",
                "entry_expiry_bars": self.config.retrace_expiry_bars,
                "management_contract": (
                    "TARGET_OR_SIGNAL_BAR_ACCEPTANCE_INVALIDATION_OR_"
                    "ONE_COMPLETED_4H_HORIZON"
                ),
                "state_sequence": [
                    "COMPLETED_5M_LIQUIDITY",
                    "COMPLETED_4H_CONTEXT_CLASSIFIED",
                    "FLOW_BREAKOUT_ACCEPTANCE_CONFIRMED",
                    "CROSS_MARKET_ACCEPTANCE_STATE_PENDING",
                    "BROKEN_LEVEL_OR_EXECUTION_VOID_RETEST_PENDING",
                    "INTRADAY_POSITION_OR_ENTRY_TERMINAL",
                ],
                "new_fitted_thresholds": [],
            },
        )
        self.used_breakouts.add(key)
        self.pending_plan_id = scenario_id
        self.pending_target_id = target.scenario_id
        self._event(
            scenario_id,
            "INTRADAY_DELIVERY_CONFIRMED",
            breakout[0],
            bar.ts_ns,
            "OBSERVE",
            "AAC_CONFIRMED",
            "KNOWN_5M_BREAKOUT_FLOW_TO_LIVE_INTERNAL_DRAW",
            breakout_level,
            {
                "direction": direction.value,
                "entry": entry,
                "stop": stop,
                "target": target.level,
                "target_id": target.scenario_id,
                "net_r": net_r,
                "context_state": context.get("state"),
                "position_expire_ts_ns": position_expire_ts_ns,
            },
        )
        self._event(
            scenario_id,
            "TRADE_PLAN_CONFIRMED",
            breakout[0],
            bar.ts_ns,
            "AAC_CONFIRMED",
            "PENDING_ENTRY",
            plan.reason_code,
            entry,
            {
                "scenario": Scenario.AAC.value,
                "direction": direction.value,
                "target": target.level,
                "stop": stop,
                "expire_ts_ns": entry_expire_ts_ns,
                "position_expire_ts_ns": position_expire_ts_ns,
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
            self.skips["INTRADAY_CAUSAL_INTERNAL_STRUCTURE_UNAVAILABLE"] += 1
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
            self.skips["INTRADAY_AMBIGUOUS_BOTH_DIRECTIONS"] += 1
            return None
        if not long_breakout and not short_breakout:
            return None

        direction = Direction.LONG if long_breakout else Direction.SHORT
        breakout = high if long_breakout else low
        target = self._nearest_internal_target(direction, bar)
        if target is None:
            self.skips["INTRADAY_NO_LIVE_INTERNAL_TARGET"] += 1
            return None
        context = self._completed_context_state(direction)
        return self._build_plan(
            bar=bar,
            prev=prev,
            atr=atr,
            direction=direction,
            breakout=breakout,
            target=target,
            relative_volume=relative_volume,
            context=context,
        )


__all__ = [
    "InternalLiquidityTarget",
    "IntradayDeliveryContinuationEngine",
]
