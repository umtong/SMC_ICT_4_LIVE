"""Causal failed-FAR acceptance continuation for Candidate 16 development.

This module adds a genuinely new scenario after, and only after, a submitted
SCDAM FAR position is completely closed by a real Nautilus STOP_MARKET child.
It does not relabel an open loss, inspect a future path, or alter the original
trade.  The failed reversal becomes new public information and starts an
independent auction state:

1. two completed closes accept beyond the original sweep extreme;
2. price returns to that failed boundary and closes back outside it;
3. a later bar reaccelerates through the frozen acceptance impulse with aligned
   taker flow, body and close location;
4. all ordinary AAC cross-market semantics still apply in the runner; and
5. the next already-existing same-side external pool must remain ahead with at
   least the unchanged after-cost 1.25R.

Risk remains the exact current-NAV 3% planned-loss budget. NautilusTrader alone
owns orders, fills, fees, margin, positions and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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

SCENARIO_KIND = "FAILED_FAR_ACCEPTANCE_CONTINUATION"


@dataclass(slots=True)
class SubmittedFarContext:
    parent_scenario_id: str
    pool_side: Side
    pool_level: float
    pool_source: str
    source_strength: int
    boundary: float
    original_direction: Direction
    original_entry: float
    original_stop: float
    original_target: float
    atr: float
    sweep_ts_ns: int
    stop_model: str


@dataclass(slots=True)
class FailedFarState:
    scenario_id: str
    parent_scenario_id: str
    side: Side
    direction: Direction
    boundary: float
    target_pool_id: str
    target_price: float
    source_pool_level: float
    source_pool_source: str
    source_strength: int
    failure_ts_ns: int
    failure_index: int
    expiry_index: int
    original_entry: float
    original_stop: float
    original_target: float
    state: str = "WAIT_ACCEPTANCE"
    outside_streak: int = 0
    acceptance_ts_ns: int | None = None
    acceptance_impulse_extreme: float | None = None
    retest_ts_ns: int | None = None
    retest_extreme: float | None = None
    reacceleration_level: float | None = None


def continuation_direction(side: Side) -> Direction:
    return Direction.LONG if side == Side.HIGH else Direction.SHORT


def market_economics(
    *,
    direction: Direction,
    entry: float,
    stop: float,
    target: float,
    taker_rate: float,
    target_maker_rate: float,
) -> tuple[float, float, float, float]:
    if direction == Direction.LONG:
        risk = entry - stop
        gross_gain = target - entry
    else:
        risk = stop - entry
        gross_gain = entry - target
    loss = risk + entry * taker_rate + stop * taker_rate
    net_gain = gross_gain - entry * taker_rate - target * target_maker_rate
    net_r = net_gain / loss if loss > 0.0 else float("-inf")
    return risk, loss, net_gain, net_r


def outside_acceptance(state: FailedFarState, bar: BarObs, atr: float, hold_atr: float) -> bool:
    allowance = hold_atr * atr
    return (
        bar.close >= state.boundary + allowance
        if state.direction == Direction.LONG
        else bar.close <= state.boundary - allowance
    )


def deep_reentry(state: FailedFarState, bar: BarObs, atr: float, retest_atr: float) -> bool:
    allowance = retest_atr * atr
    return (
        bar.close < state.boundary - allowance
        if state.direction == Direction.LONG
        else bar.close > state.boundary + allowance
    )


def defended_boundary_retest(
    state: FailedFarState,
    bar: BarObs,
    atr: float,
    *,
    hold_atr: float,
    retest_atr: float,
) -> bool:
    hold = hold_atr * atr
    reach = retest_atr * atr
    if state.direction == Direction.LONG:
        return (
            bar.low <= state.boundary + reach
            and bar.close >= state.boundary + hold
            and bar.close_location >= 0.50
        )
    return (
        bar.high >= state.boundary - reach
        and bar.close <= state.boundary - hold
        and bar.close_location <= 0.50
    )


def _terminal(self: CausalAuctionEngine, state: FailedFarState, bar: BarObs, reason: str) -> None:
    self._event(
        state.scenario_id,
        "FAILED_FAR_CONTINUATION_TERMINAL",
        state.failure_ts_ns,
        bar.ts_ns,
        state.state,
        "TERMINAL",
        reason,
        state.boundary,
        {
            "parent_scenario_id": state.parent_scenario_id,
            "target_pool": state.target_pool_id,
            "target": state.target_price,
        },
    )
    self.skips[reason] += 1
    self._candidate16_failed_far_state = None


def _target_is_live(self: CausalAuctionEngine, state: FailedFarState) -> bool:
    return any(
        pool.scenario_id == state.target_pool_id
        and not pool.consumed
        and self._index <= pool.expiry_index
        for pool in self.pools
    )


def _step(self: CausalAuctionEngine, bar: BarObs) -> TradePlan | None:
    state: FailedFarState | None = getattr(self, "_candidate16_failed_far_state", None)
    if state is None:
        return None
    atr = self.atr
    if atr is None or atr <= 0.0:
        return None
    if self._index > state.expiry_index:
        _terminal(self, state, bar, "FAILED_FAR_CONTINUATION_EXPIRED")
        return None
    if not _target_is_live(self, state):
        _terminal(self, state, bar, "FAILED_FAR_TARGET_NO_LONGER_LIVE")
        return None
    target_reached = (
        bar.high >= state.target_price
        if state.direction == Direction.LONG
        else bar.low <= state.target_price
    )
    if target_reached:
        _terminal(self, state, bar, "FAILED_FAR_TARGET_REACHED_BEFORE_ENTRY")
        return None
    if deep_reentry(self, state, bar, atr, self.config.acceptance_retest_atr):
        _terminal(self, state, bar, "FAILED_FAR_ACCEPTANCE_REJECTED")
        return None

    if state.state == "WAIT_ACCEPTANCE":
        if outside_acceptance(state, bar, atr, self.config.acceptance_hold_atr):
            state.outside_streak += 1
            if state.direction == Direction.LONG:
                state.acceptance_impulse_extreme = max(
                    state.acceptance_impulse_extreme or bar.high,
                    bar.high,
                )
            else:
                state.acceptance_impulse_extreme = min(
                    state.acceptance_impulse_extreme or bar.low,
                    bar.low,
                )
        else:
            state.outside_streak = 0
            state.acceptance_impulse_extreme = None
        if state.outside_streak >= self.config.acceptance_min_closes:
            state.state = "WAIT_RETEST"
            state.acceptance_ts_ns = bar.ts_ns
            self._event(
                state.scenario_id,
                "FAILED_FAR_ACCEPTANCE_CONFIRMED",
                state.failure_ts_ns,
                bar.ts_ns,
                "WAIT_ACCEPTANCE",
                "WAIT_RETEST",
                "TWO_CLOSES_BEYOND_FAILED_SWEEP_BOUNDARY",
                state.boundary,
                {
                    "direction": state.direction.value,
                    "outside_closes": state.outside_streak,
                    "acceptance_impulse_extreme": state.acceptance_impulse_extreme,
                },
            )
        return None

    if state.state == "WAIT_RETEST":
        if state.direction == Direction.LONG:
            state.acceptance_impulse_extreme = max(
                float(state.acceptance_impulse_extreme),
                bar.high,
            )
        else:
            state.acceptance_impulse_extreme = min(
                float(state.acceptance_impulse_extreme),
                bar.low,
            )
        if defended_boundary_retest(
            state,
            bar,
            atr,
            hold_atr=self.config.acceptance_hold_atr,
            retest_atr=self.config.acceptance_retest_atr,
        ):
            state.state = "WAIT_REACCELERATION"
            state.retest_ts_ns = bar.ts_ns
            state.retest_extreme = bar.low if state.direction == Direction.LONG else bar.high
            state.reacceleration_level = float(state.acceptance_impulse_extreme)
            self._event(
                state.scenario_id,
                "FAILED_FAR_BOUNDARY_RETEST_DEFENDED",
                state.failure_ts_ns,
                bar.ts_ns,
                "WAIT_RETEST",
                "WAIT_REACCELERATION",
                "FAILED_SWEEP_BOUNDARY_RETEST_CLOSED_OUTSIDE",
                state.boundary,
                {
                    "retest_extreme": state.retest_extreme,
                    "reacceleration_level": state.reacceleration_level,
                },
            )
        return None

    if state.state != "WAIT_REACCELERATION":
        return None

    # A later defended retest may improve the structural invalidation without
    # changing the already-frozen reacceleration level.
    if outside_acceptance(state, bar, atr, self.config.acceptance_hold_atr):
        if state.direction == Direction.LONG:
            state.retest_extreme = min(float(state.retest_extreme), bar.low)
        else:
            state.retest_extreme = max(float(state.retest_extreme), bar.high)

    if state.direction == Direction.LONG:
        reaccelerated = bar.close > float(state.reacceleration_level)
        flow = bar.signed_flow >= self.config.reacceleration_flow_min
        location = bar.close_location >= 0.60
        stop = min(float(state.retest_extreme), state.boundary) - self.config.stop_buffer_atr * atr
    else:
        reaccelerated = bar.close < float(state.reacceleration_level)
        flow = bar.signed_flow <= -self.config.reacceleration_flow_min
        location = bar.close_location <= 0.40
        stop = max(float(state.retest_extreme), state.boundary) + self.config.stop_buffer_atr * atr
    body = bar.body >= self.config.reacceleration_body_atr * atr
    if not (reaccelerated and flow and location and body):
        return None

    entry = bar.close
    risk, loss, net_gain, net_r = market_economics(
        direction=state.direction,
        entry=entry,
        stop=stop,
        target=state.target_price,
        taker_rate=self.config.effective_taker_rate,
        target_maker_rate=self.config.effective_maker_rate,
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
        _terminal(self, state, bar, "FAILED_FAR_CONTINUATION_INSUFFICIENT_COSTED_R")
        return None

    plan = TradePlan(
        scenario_id=state.scenario_id,
        scenario=Scenario.AAC,
        direction=state.direction,
        observed_ts_ns=bar.ts_ns,
        expected_entry=entry,
        stop_price=stop,
        target_price=state.target_price,
        atr=atr,
        loss_per_unit=loss,
        gain_per_unit=net_gain,
        net_r=net_r,
        reason_code="FAILED_FAR_ACCEPTANCE_RETEST_REACCELERATION_MARKET",
        expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,
        entry_order_type="MARKET",
        entry_post_only=False,
        details={
            "scenario_kind": SCENARIO_KIND,
            "parent_scenario_id": state.parent_scenario_id,
            "failure_ts_ns": state.failure_ts_ns,
            "sweep_ts_ns": state.failure_ts_ns,
            "failed_boundary": state.boundary,
            "source_pool_level": state.source_pool_level,
            "source_pool_source": state.source_pool_source,
            "source_strength": state.source_strength,
            "acceptance_ts_ns": state.acceptance_ts_ns,
            "retest_ts_ns": state.retest_ts_ns,
            "retest_extreme": state.retest_extreme,
            "reacceleration_level": state.reacceleration_level,
            "target_pool": state.target_pool_id,
            "entry_model": "FAILED_BOUNDARY_RETEST_REACCELERATION_MARKET",
            "stop_model": "FAILED_BOUNDARY_REACCEPTANCE",
            "entry_cost_assumption": "TAKER",
            "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,
            "original_far_entry": state.original_entry,
            "original_far_stop": state.original_stop,
            "original_far_target": state.original_target,
        },
    )
    self._event(
        state.scenario_id,
        "TRADE_PLAN_CONFIRMED",
        state.failure_ts_ns,
        bar.ts_ns,
        "WAIT_REACCELERATION",
        "PLAN_CONFIRMED",
        plan.reason_code,
        entry,
        {
            "scenario": Scenario.AAC.value,
            "scenario_kind": SCENARIO_KIND,
            "direction": state.direction.value,
            "entry_order_type": "MARKET",
            "entry_post_only": False,
            "target": state.target_price,
            "stop": stop,
            "net_r": net_r,
        },
    )
    state.state = "PLAN_CONFIRMED"
    return plan


BASE_ON_BAR = CausalAuctionEngine.on_bar
BASE_MARK_SUBMITTED = CausalAuctionEngine.mark_submitted
BASE_MARK_REJECTED = CausalAuctionEngine.mark_rejected
BASE_MARK_TRADE_TERMINAL = CausalAuctionEngine.mark_trade_terminal


def candidate16_on_bar(
    self: CausalAuctionEngine,
    bar: BarObs,
    *,
    allow_entry: bool = True,
) -> TradePlan | None:
    state: FailedFarState | None = getattr(self, "_candidate16_failed_far_state", None)
    if state is None or self.active_trade_id is not None:
        return BASE_ON_BAR(self, bar, allow_entry=allow_entry)

    # Reuse the exact base updater while preventing a second overlapping sweep
    # detector on this instrument. This sentinel is local scenario state, not a
    # portfolio slot or position.
    self.active_trade_id = state.scenario_id
    self.active_trade_state = state.state
    try:
        base_plan = BASE_ON_BAR(self, bar, allow_entry=allow_entry)
    finally:
        self.active_trade_id = None
        self.active_trade_state = None
    if base_plan is not None:
        raise RuntimeError("failed-FAR sentinel unexpectedly emitted a base plan")
    plan = _step(self, bar)
    if plan is not None and not allow_entry:
        candidate16_mark_rejected(self, plan, bar.ts_ns, "OUTSIDE_EVALUATION_WINDOW")
        return None
    return plan


def candidate16_mark_submitted(
    self: CausalAuctionEngine,
    plan: TradePlan,
    quantity: Any,
    details: dict[str, Any] | None = None,
) -> None:
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        state: FailedFarState | None = getattr(self, "_candidate16_failed_far_state", None)
        if state is None or state.scenario_id != plan.scenario_id or state.state != "PLAN_CONFIRMED":
            raise RuntimeError("submitted failed-FAR plan does not match pending state")
        if self.active_trade_id is not None:
            raise RuntimeError("global candidate slot already occupied")
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
        self._candidate16_trade_kind = SCENARIO_KIND
        self._candidate16_failed_far_state = None
        self._candidate16_submitted_far = None
        return

    context: SubmittedFarContext | None = None
    active = self.active
    if (
        plan.scenario == Scenario.FAR
        and active is not None
        and active.pool.scenario_id == plan.scenario_id
        and str(plan.details.get("stop_model", "")) == "SWEEP_EXTREME_INVALIDATION"
    ):
        context = SubmittedFarContext(
            parent_scenario_id=plan.scenario_id,
            pool_side=active.pool.side,
            pool_level=active.pool.level,
            pool_source=active.pool.source,
            source_strength=active.pool.strength,
            boundary=active.sweep_extreme,
            original_direction=plan.direction,
            original_entry=plan.expected_entry,
            original_stop=plan.stop_price,
            original_target=plan.target_price,
            atr=plan.atr,
            sweep_ts_ns=int(plan.details.get("sweep_ts_ns", active.sweep.ts_ns)),
            stop_model="SWEEP_EXTREME_INVALIDATION",
        )
    BASE_MARK_SUBMITTED(self, plan, quantity, details)
    self._candidate16_submitted_far = context
    self._candidate16_trade_kind = "ORIGINAL_FAR" if context is not None else "OTHER"


def candidate16_mark_rejected(
    self: CausalAuctionEngine,
    plan: TradePlan,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        state: FailedFarState | None = getattr(self, "_candidate16_failed_far_state", None)
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
        self._candidate16_failed_far_state = None
        return
    BASE_MARK_REJECTED(self, plan, ts_ns, reason, details)


def candidate16_mark_trade_terminal(
    self: CausalAuctionEngine,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    trade_kind = getattr(self, "_candidate16_trade_kind", None)
    context: SubmittedFarContext | None = getattr(self, "_candidate16_submitted_far", None)
    BASE_MARK_TRADE_TERMINAL(self, ts_ns, reason, details)
    self._candidate16_trade_kind = None
    self._candidate16_submitted_far = None

    if trade_kind != "ORIGINAL_FAR" or context is None or reason != "STOP_MARKET_FILLED":
        return
    if not self.bars:
        return
    reference = self.bars[-1].close
    target = self._next_pool(context.pool_side, reference, min_strength=1)
    if target is None:
        self.skips["FAILED_FAR_NO_SAME_SIDE_EXTERNAL_TARGET"] += 1
        self._event(
            f"{context.parent_scenario_id}-FAILED-FAR-{ts_ns}",
            "FAILED_FAR_CONTINUATION_TERMINAL",
            ts_ns,
            ts_ns,
            "POSITION",
            "TERMINAL",
            "FAILED_FAR_NO_SAME_SIDE_EXTERNAL_TARGET",
            context.boundary,
        )
        return
    direction = continuation_direction(context.pool_side)
    target_ahead = target.level > reference if direction == Direction.LONG else target.level < reference
    if not target_ahead:
        return
    scenario_id = f"{context.parent_scenario_id}-FAILED-FAR-{ts_ns}"
    state = FailedFarState(
        scenario_id=scenario_id,
        parent_scenario_id=context.parent_scenario_id,
        side=context.pool_side,
        direction=direction,
        boundary=context.boundary,
        target_pool_id=target.scenario_id,
        target_price=target.level,
        source_pool_level=context.pool_level,
        source_pool_source=context.pool_source,
        source_strength=context.source_strength,
        failure_ts_ns=ts_ns,
        failure_index=self._index,
        expiry_index=self._index + self.config.event_expiry_bars,
        original_entry=context.original_entry,
        original_stop=context.original_stop,
        original_target=context.original_target,
    )
    self._candidate16_failed_far_state = state
    self._event(
        scenario_id,
        "FAILED_FAR_INVALIDATED",
        ts_ns,
        ts_ns,
        "POSITION",
        "WAIT_ACCEPTANCE",
        "NAUTILUS_STOP_MARKET_CONFIRMED_SWEEP_FAILURE",
        context.boundary,
        {
            "parent_scenario_id": context.parent_scenario_id,
            "continuation_direction": direction.value,
            "failed_boundary": context.boundary,
            "target_pool": target.scenario_id,
            "target": target.level,
            "original_far_entry": context.original_entry,
            "original_far_stop": context.original_stop,
            "original_far_target": context.original_target,
        },
    )


def install() -> None:
    CausalAuctionEngine.on_bar = candidate16_on_bar
    CausalAuctionEngine.mark_submitted = candidate16_mark_submitted
    CausalAuctionEngine.mark_rejected = candidate16_mark_rejected
    CausalAuctionEngine.mark_trade_terminal = candidate16_mark_trade_terminal
