"""Candidate 32: repeated internal-pool effort/result absorption reversal.

Candidate 26 showed that one internal raid plus reclaim is usually continuation,
not reversal. Candidate 32 requires a materially different auction sequence:

1. a previously known five-minute internal pivot is traded through with
   directional aggressor flow;
2. price later completes a reclaim of that pivot;
3. the same pivot is traded through again;
4. the second test has at least as much absolute aggressor flow but no greater
   ATR-normalized penetration (more effort, no more result); and
5. a later completed bar reclaims with opposite flow, body and close location.

Only then may a market FAR plan target frozen pre-existing external liquidity on
the opposite side. The numeric thresholds, cost floor, raid-extreme stop, exact
3% current-NAV risk and global one-slot allocator are inherited unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from logic import (
    BarObs,
    CausalAuctionEngine,
    Direction,
    MINUTE_NS,
    Scenario,
    Side,
    TradePlan,
)
from semantic_execution import MARKET_ENTRY_SENTINEL_NS

SCENARIO_KIND = "REPEATED_INTERNAL_POOL_EFFORT_RESULT_ABSORPTION"


@dataclass(slots=True)
class EffortResultState:
    scenario_id: str
    swept_side: Side
    direction: Direction
    pivot_candidate_ts_ns: int
    pivot_known_ts_ns: int
    pivot_level: float
    target_pool_id: str
    target_price: float
    target_source: str
    first_test_ts_ns: int
    first_test_index: int
    first_penetration_atr: float
    first_abs_flow: float
    first_extreme: float
    expiry_index: int
    state: str = "WAIT_FIRST_RECLAIM"
    accepted_outside_streak: int = 0
    second_test_ts_ns: int | None = None
    second_test_index: int | None = None
    second_penetration_atr: float | None = None
    second_abs_flow: float | None = None
    second_extreme: float | None = None


def _market_economics(
    *,
    direction: Direction,
    entry: float,
    stop: float,
    target: float,
    taker_rate: float,
    maker_rate: float,
) -> tuple[float, float, float, float]:
    if direction == Direction.LONG:
        risk = entry - stop
        gross_gain = target - entry
    else:
        risk = stop - entry
        gross_gain = entry - target
    loss = risk + entry * taker_rate + stop * taker_rate
    net_gain = gross_gain - entry * taker_rate - target * maker_rate
    net_r = net_gain / loss if loss > 0.0 else float("-inf")
    return risk, loss, net_gain, net_r


def _consumed_keys(self: CausalAuctionEngine) -> set[tuple[str, int, int, float]]:
    keys = getattr(self, "_candidate32_consumed_internal_keys", None)
    if keys is None:
        keys = set()
        self._candidate32_consumed_internal_keys = keys
    return keys


def _target_is_live(self: CausalAuctionEngine, state: EffortResultState) -> bool:
    return any(
        pool.scenario_id == state.target_pool_id
        and not pool.consumed
        and self._index <= pool.expiry_index
        for pool in self.pools
    )


def _terminal(
    self: CausalAuctionEngine,
    state: EffortResultState,
    bar: BarObs,
    reason: str,
) -> None:
    self._event(
        state.scenario_id,
        "EFFORT_RESULT_ABSORPTION_TERMINAL",
        state.first_test_ts_ns,
        bar.ts_ns,
        state.state,
        "TERMINAL",
        reason,
        state.pivot_level,
        {
            "swept_side": state.swept_side.value,
            "direction": state.direction.value,
            "pivot_level": state.pivot_level,
            "first_penetration_atr": state.first_penetration_atr,
            "first_abs_flow": state.first_abs_flow,
            "second_penetration_atr": state.second_penetration_atr,
            "second_abs_flow": state.second_abs_flow,
            "target_pool": state.target_pool_id,
            "target": state.target_price,
        },
    )
    self.skips[reason] += 1
    self._candidate32_effort_result_state = None


def _select_first_test(
    self: CausalAuctionEngine,
    bar: BarObs,
    prev: BarObs,
    atr: float,
) -> tuple[Side, int, int, float, float] | None:
    cutoff = bar.ts_ns - self.config.event_expiry_bars * MINUTE_NS
    consumed = _consumed_keys(self)
    crossed: list[tuple[Side, int, int, float, float]] = []
    for side, points in ((Side.HIGH, self.internal_highs), (Side.LOW, self.internal_lows)):
        for candidate_ts_ns, known_ts_ns, level in points:
            key = (side.value, int(candidate_ts_ns), int(known_ts_ns), round(float(level), 10))
            if key in consumed or known_ts_ns >= bar.ts_ns or known_ts_ns < cutoff:
                continue
            if side == Side.HIGH:
                did_cross = prev.close <= level < bar.high
                penetration = (bar.high - level) / atr
                flow_ok = bar.signed_flow >= self.config.absorption_flow_min
            else:
                did_cross = prev.close >= level > bar.low
                penetration = (level - bar.low) / atr
                flow_ok = bar.signed_flow <= -self.config.absorption_flow_min
            tolerance = 1e-12
            if (
                did_cross
                and flow_ok
                and self.config.sweep_min_atr - tolerance
                <= penetration
                <= self.config.sweep_max_atr + tolerance
            ):
                crossed.append(
                    (
                        side,
                        int(candidate_ts_ns),
                        int(known_ts_ns),
                        float(level),
                        float(penetration),
                    )
                )
    if not crossed:
        return None
    # The nearest crossed resting pool is the first reachable internal auction.
    highs = [item for item in crossed if item[0] == Side.HIGH]
    lows = [item for item in crossed if item[0] == Side.LOW]
    if highs and lows:
        return None
    return min(highs, key=lambda item: item[3]) if highs else max(lows, key=lambda item: item[3])


def _detect(
    self: CausalAuctionEngine,
    bar: BarObs,
    prev: BarObs,
    atr: float,
) -> None:
    if getattr(self, "_candidate32_effort_result_state", None) is not None:
        return
    median_volume = self.median_volume
    if median_volume is None or median_volume <= 0.0:
        return
    relative_volume = bar.volume / median_volume
    if relative_volume < self.config.min_relative_volume:
        return

    test = _select_first_test(self, bar, prev, atr)
    if test is None:
        return
    side, candidate_ts_ns, known_ts_ns, level, penetration = test
    direction = Direction.SHORT if side == Side.HIGH else Direction.LONG
    target_side = Side.LOW if side == Side.HIGH else Side.HIGH
    target = self._next_pool(target_side, bar.close, min_strength=1)
    if target is None or target.confirmed_index >= self._index:
        return
    target_ahead = target.level < bar.close if direction == Direction.SHORT else target.level > bar.close
    if not target_ahead:
        return

    key = (side.value, candidate_ts_ns, known_ts_ns, round(level, 10))
    _consumed_keys(self).add(key)
    scenario_id = f"{self.instrument_id}-ERA-{known_ts_ns}-{bar.ts_ns}-{direction.value}"
    state = EffortResultState(
        scenario_id=scenario_id,
        swept_side=side,
        direction=direction,
        pivot_candidate_ts_ns=candidate_ts_ns,
        pivot_known_ts_ns=known_ts_ns,
        pivot_level=level,
        target_pool_id=target.scenario_id,
        target_price=float(target.level),
        target_source=str(target.source),
        first_test_ts_ns=bar.ts_ns,
        first_test_index=self._index,
        first_penetration_atr=penetration,
        first_abs_flow=abs(bar.signed_flow),
        first_extreme=bar.high if side == Side.HIGH else bar.low,
        expiry_index=self._index + self.config.event_expiry_bars,
    )
    self._candidate32_effort_result_state = state
    self._event(
        scenario_id,
        "EFFORT_RESULT_FIRST_TEST",
        candidate_ts_ns,
        bar.ts_ns,
        "INTERNAL_POOL_ARMED",
        "WAIT_FIRST_RECLAIM",
        "PREKNOWN_INTERNAL_POOL_TRADED_THROUGH_WITH_AGGRESSOR_FLOW",
        level,
        {
            "swept_side": side.value,
            "direction": direction.value,
            "pivot_known_ts_ns": known_ts_ns,
            "penetration_atr": penetration,
            "absolute_signed_flow": abs(bar.signed_flow),
            "relative_volume": relative_volume,
            "target_pool": target.scenario_id,
            "target": target.level,
            "target_source": target.source,
        },
    )


def _reset_first_test(
    self: CausalAuctionEngine,
    state: EffortResultState,
    bar: BarObs,
    penetration: float,
) -> None:
    state.first_test_ts_ns = bar.ts_ns
    state.first_test_index = self._index
    state.first_penetration_atr = penetration
    state.first_abs_flow = abs(bar.signed_flow)
    state.first_extreme = bar.high if state.swept_side == Side.HIGH else bar.low
    state.state = "WAIT_FIRST_RECLAIM"
    state.accepted_outside_streak = 0
    state.second_test_ts_ns = None
    state.second_test_index = None
    state.second_penetration_atr = None
    state.second_abs_flow = None
    state.second_extreme = None
    self._event(
        state.scenario_id,
        "EFFORT_RESULT_BENCHMARK_ADVANCED",
        state.first_test_ts_ns,
        bar.ts_ns,
        "WAIT_SECOND_TEST",
        "WAIT_FIRST_RECLAIM",
        "GREATER_PRICE_RESULT_RESETS_ABSORPTION_BENCHMARK",
        state.pivot_level,
        {
            "penetration_atr": penetration,
            "absolute_signed_flow": abs(bar.signed_flow),
        },
    )


def _plan(
    self: CausalAuctionEngine,
    state: EffortResultState,
    bar: BarObs,
    atr: float,
) -> TradePlan | None:
    extremes = [state.first_extreme]
    if state.second_extreme is not None:
        extremes.append(state.second_extreme)
    stop = (
        min(extremes) - self.config.stop_buffer_atr * atr
        if state.direction == Direction.LONG
        else max(extremes) + self.config.stop_buffer_atr * atr
    )
    entry = bar.close
    risk, loss, net_gain, net_r = _market_economics(
        direction=state.direction,
        entry=entry,
        stop=stop,
        target=state.target_price,
        taker_rate=self.config.effective_taker_rate,
        maker_rate=self.config.effective_maker_rate,
    )
    causal_order = (
        stop < entry < state.target_price
        if state.direction == Direction.LONG
        else state.target_price < entry < stop
    )
    if (
        not causal_order
        or risk <= 0.0
        or risk / atr < self.config.min_stop_atr
        or net_gain <= 0.0
        or net_r < self.config.min_net_r
    ):
        _terminal(self, state, bar, "EFFORT_RESULT_ABSORPTION_INSUFFICIENT_COSTED_R")
        return None

    plan = TradePlan(
        scenario_id=state.scenario_id,
        scenario=Scenario.FAR,
        direction=state.direction,
        observed_ts_ns=bar.ts_ns,
        expected_entry=entry,
        stop_price=stop,
        target_price=state.target_price,
        atr=atr,
        loss_per_unit=loss,
        gain_per_unit=net_gain,
        net_r=net_r,
        reason_code="REPEATED_INTERNAL_POOL_EFFORT_RESULT_ABSORPTION_MARKET",
        expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,
        entry_order_type="MARKET",
        entry_post_only=False,
        details={
            "scenario_kind": SCENARIO_KIND,
            "sweep_ts_ns": state.second_test_ts_ns,
            "pivot_candidate_ts_ns": state.pivot_candidate_ts_ns,
            "pivot_known_ts_ns": state.pivot_known_ts_ns,
            "pivot_level": state.pivot_level,
            "swept_side": state.swept_side.value,
            "first_test_ts_ns": state.first_test_ts_ns,
            "first_penetration_atr": state.first_penetration_atr,
            "first_abs_flow": state.first_abs_flow,
            "second_test_ts_ns": state.second_test_ts_ns,
            "second_penetration_atr": state.second_penetration_atr,
            "second_abs_flow": state.second_abs_flow,
            "target_pool": state.target_pool_id,
            "target_source": state.target_source,
            "entry_model": "POST_SECOND_TEST_RECLAIM_MARKET",
            "stop_model": "MAXIMUM_REPEATED_RAID_EXTREME",
            "entry_cost_assumption": "TAKER",
            "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,
        },
    )
    self._event(
        state.scenario_id,
        "TRADE_PLAN_CONFIRMED",
        int(state.second_test_ts_ns or state.first_test_ts_ns),
        bar.ts_ns,
        "WAIT_FINAL_RECLAIM",
        "PLAN_CONFIRMED",
        plan.reason_code,
        entry,
        {
            "scenario": Scenario.FAR.value,
            "scenario_kind": SCENARIO_KIND,
            "direction": state.direction.value,
            "entry_order_type": "MARKET",
            "entry_post_only": False,
            "stop": stop,
            "target": state.target_price,
            "net_r": net_r,
        },
    )
    state.state = "PLAN_CONFIRMED"
    return plan


def _step(
    self: CausalAuctionEngine,
    state: EffortResultState,
    bar: BarObs,
    prev: BarObs,
    atr: float,
) -> TradePlan | None:
    if self._index > state.expiry_index:
        _terminal(self, state, bar, "EFFORT_RESULT_ABSORPTION_EXPIRED")
        return None
    if not _target_is_live(self, state):
        _terminal(self, state, bar, "EFFORT_RESULT_EXTERNAL_TARGET_NO_LONGER_LIVE")
        return None
    target_reached = (
        bar.low <= state.target_price
        if state.direction == Direction.SHORT
        else bar.high >= state.target_price
    )
    if target_reached:
        _terminal(self, state, bar, "EFFORT_RESULT_TARGET_REACHED_BEFORE_ENTRY")
        return None

    if state.swept_side == Side.HIGH:
        outside = bar.close >= state.pivot_level + self.config.acceptance_close_atr * atr
        reclaimed = bar.close <= state.pivot_level - self.config.rejection_reclaim_atr * atr
        crossed = prev.close <= state.pivot_level < bar.high
        penetration = (bar.high - state.pivot_level) / atr
        same_flow = bar.signed_flow >= self.config.absorption_flow_min
    else:
        outside = bar.close <= state.pivot_level - self.config.acceptance_close_atr * atr
        reclaimed = bar.close >= state.pivot_level + self.config.rejection_reclaim_atr * atr
        crossed = prev.close >= state.pivot_level > bar.low
        penetration = (state.pivot_level - bar.low) / atr
        same_flow = bar.signed_flow <= -self.config.absorption_flow_min

    if state.state == "WAIT_FIRST_RECLAIM":
        if self._index <= state.first_test_index:
            return None
        state.accepted_outside_streak = state.accepted_outside_streak + 1 if outside else 0
        if state.accepted_outside_streak >= self.config.acceptance_min_closes:
            _terminal(self, state, bar, "FIRST_INTERNAL_TEST_ACCEPTED")
            return None
        if reclaimed:
            state.state = "WAIT_SECOND_TEST"
            state.accepted_outside_streak = 0
            self._event(
                state.scenario_id,
                "EFFORT_RESULT_FIRST_RECLAIM",
                state.first_test_ts_ns,
                bar.ts_ns,
                "WAIT_FIRST_RECLAIM",
                "WAIT_SECOND_TEST",
                "FIRST_INTERNAL_TEST_RECLAIMED",
                state.pivot_level,
            )
        return None

    if state.state == "WAIT_SECOND_TEST":
        median_volume = self.median_volume
        relative_volume = 0.0 if not median_volume else bar.volume / median_volume
        if not (
            crossed
            and same_flow
            and relative_volume >= self.config.min_relative_volume
            and self.config.sweep_min_atr <= penetration <= self.config.sweep_max_atr
        ):
            return None
        effort_not_less = abs(bar.signed_flow) >= state.first_abs_flow
        result_not_greater = penetration <= state.first_penetration_atr + 1e-12
        if effort_not_less and result_not_greater:
            state.state = "WAIT_FINAL_RECLAIM"
            state.second_test_ts_ns = bar.ts_ns
            state.second_test_index = self._index
            state.second_penetration_atr = penetration
            state.second_abs_flow = abs(bar.signed_flow)
            state.second_extreme = bar.high if state.swept_side == Side.HIGH else bar.low
            state.accepted_outside_streak = 0
            self._event(
                state.scenario_id,
                "EFFORT_RESULT_ABSORPTION_CONFIRMED",
                state.first_test_ts_ns,
                bar.ts_ns,
                "WAIT_SECOND_TEST",
                "WAIT_FINAL_RECLAIM",
                "GREATER_OR_EQUAL_EFFORT_WITHOUT_GREATER_PRICE_RESULT",
                state.pivot_level,
                {
                    "first_penetration_atr": state.first_penetration_atr,
                    "first_abs_flow": state.first_abs_flow,
                    "second_penetration_atr": penetration,
                    "second_abs_flow": abs(bar.signed_flow),
                },
            )
            return None
        if effort_not_less and penetration > state.first_penetration_atr + 1e-12:
            _reset_first_test(self, state, bar, penetration)
        return None

    if state.state != "WAIT_FINAL_RECLAIM":
        return None
    if state.second_test_index is None or self._index <= state.second_test_index:
        return None

    # A fresh deeper test disproves the current absorption comparison and becomes
    # the new benchmark rather than being silently ignored.
    if crossed and same_flow and penetration > state.first_penetration_atr + 1e-12:
        _reset_first_test(self, state, bar, penetration)
        return None

    if state.direction == Direction.SHORT:
        directional_flow = bar.signed_flow <= -self.config.displacement_flow_min
        directional_location = bar.close_location <= 1.0 - self.config.acceptance_close_location
    else:
        directional_flow = bar.signed_flow >= self.config.displacement_flow_min
        directional_location = bar.close_location >= self.config.acceptance_close_location
    directional_body = bar.body >= self.config.displacement_body_atr * atr
    if not (reclaimed and directional_flow and directional_location and directional_body):
        return None
    self._event(
        state.scenario_id,
        "EFFORT_RESULT_FINAL_RECLAIM",
        int(state.second_test_ts_ns or state.first_test_ts_ns),
        bar.ts_ns,
        "WAIT_FINAL_RECLAIM",
        "PLAN_PENDING_COST_GATE",
        "ABSORBED_SECOND_TEST_RECLAIMED_WITH_OPPOSITE_DELIVERY",
        state.pivot_level,
        {
            "reclaim_close": bar.close,
            "reclaim_signed_flow": bar.signed_flow,
            "reclaim_body_atr": bar.body / atr,
            "reclaim_close_location": bar.close_location,
        },
    )
    return _plan(self, state, bar, atr)


BASE_ON_BAR: Callable[..., TradePlan | None] | None = None
BASE_MARK_SUBMITTED: Callable[..., None] | None = None
BASE_MARK_REJECTED: Callable[..., None] | None = None
BASE_MARK_TRADE_TERMINAL: Callable[..., None] | None = None


def candidate32_on_bar(
    self: CausalAuctionEngine,
    bar: BarObs,
    *,
    allow_entry: bool = True,
) -> TradePlan | None:
    if BASE_ON_BAR is None:
        raise RuntimeError("Candidate 32 is not installed")
    base_plan = BASE_ON_BAR(self, bar, allow_entry=allow_entry)
    if base_plan is not None:
        return base_plan
    if self.active_trade_id is not None:
        return None
    if getattr(self, "_candidate16_failed_far_state", None) is not None:
        return None
    atr = self.atr
    if atr is None or atr <= 0.0 or len(self.bars) < 2:
        return None
    prev = self.bars[-2]
    state: EffortResultState | None = getattr(
        self,
        "_candidate32_effort_result_state",
        None,
    )
    if state is None:
        _detect(self, bar, prev, atr)
        state = getattr(self, "_candidate32_effort_result_state", None)
        if state is None:
            return None
    plan = _step(self, state, bar, prev, atr)
    if plan is not None and not allow_entry:
        candidate32_mark_rejected(
            self,
            plan,
            bar.ts_ns,
            "OUTSIDE_EVALUATION_WINDOW",
        )
        return None
    return plan


def candidate32_mark_submitted(
    self: CausalAuctionEngine,
    plan: TradePlan,
    quantity: Any,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_SUBMITTED is None:
        raise RuntimeError("Candidate 32 is not installed")
    state: EffortResultState | None = getattr(
        self,
        "_candidate32_effort_result_state",
        None,
    )
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        if state is None or state.scenario_id != plan.scenario_id or state.state != "PLAN_CONFIRMED":
            raise RuntimeError("submitted effort/result plan does not match pending state")
        if self.active_trade_id is not None:
            raise RuntimeError("global candidate slot already occupied")
        if self.active is not None and self.bars:
            self._terminal(
                self.active,
                self.bars[-1],
                "EFFORT_RESULT_ALLOCATED_BEFORE_EXTERNAL_PLAN",
            )
        self._event(
            plan.scenario_id,
            "ENTRY_ORDER_LIST_SUBMITTED",
            plan.observed_ts_ns,
            plan.observed_ts_ns,
            "PLAN_CONFIRMED",
            "PENDING_ENTRY",
            plan.reason_code,
            plan.expected_entry,
            {
                "scenario": plan.scenario.value,
                "scenario_kind": SCENARIO_KIND,
                "direction": plan.direction.value,
                "quantity": str(quantity),
                "net_r": plan.net_r,
                **(details or {}),
            },
        )
        self.active_trade_id = plan.scenario_id
        self.active_trade_state = "PENDING_ENTRY"
        self._candidate32_effort_result_state = None
        self._candidate16_trade_kind = "OTHER"
        self._candidate16_submitted_far = None
        return
    if state is not None and self.bars:
        _terminal(self, state, self.bars[-1], "COMPETING_EXTERNAL_PLAN_ALLOCATED")
    BASE_MARK_SUBMITTED(self, plan, quantity, details)


def candidate32_mark_rejected(
    self: CausalAuctionEngine,
    plan: TradePlan,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_REJECTED is None:
        raise RuntimeError("Candidate 32 is not installed")
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        state: EffortResultState | None = getattr(
            self,
            "_candidate32_effort_result_state",
            None,
        )
        if state is None or state.scenario_id != plan.scenario_id:
            return
        self._event(
            plan.scenario_id,
            "ENTRY_PLAN_REJECTED",
            plan.observed_ts_ns,
            ts_ns,
            state.state,
            "TERMINAL",
            reason,
            plan.expected_entry,
            details or {},
        )
        self.skips[reason] += 1
        self._candidate32_effort_result_state = None
        return
    BASE_MARK_REJECTED(self, plan, ts_ns, reason, details)


def candidate32_mark_trade_terminal(
    self: CausalAuctionEngine,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_TRADE_TERMINAL is None:
        raise RuntimeError("Candidate 32 is not installed")
    BASE_MARK_TRADE_TERMINAL(self, ts_ns, reason, details)


def install() -> None:
    global BASE_ON_BAR, BASE_MARK_SUBMITTED, BASE_MARK_REJECTED, BASE_MARK_TRADE_TERMINAL
    if CausalAuctionEngine.on_bar is candidate32_on_bar:
        return
    BASE_ON_BAR = CausalAuctionEngine.on_bar
    BASE_MARK_SUBMITTED = CausalAuctionEngine.mark_submitted
    BASE_MARK_REJECTED = CausalAuctionEngine.mark_rejected
    BASE_MARK_TRADE_TERMINAL = CausalAuctionEngine.mark_trade_terminal
    CausalAuctionEngine.on_bar = candidate32_on_bar
    CausalAuctionEngine.mark_submitted = candidate32_mark_submitted
    CausalAuctionEngine.mark_rejected = candidate32_mark_rejected
    CausalAuctionEngine.mark_trade_terminal = candidate32_mark_trade_terminal
