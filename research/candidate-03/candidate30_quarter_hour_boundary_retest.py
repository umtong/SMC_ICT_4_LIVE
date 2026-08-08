"""Candidate 30: passive boundary retest after a quarter-hour failed auction.

Candidate 29 discovered thousands of causally complete prior-quarter sweeps and
reclaims, but its still-later market-displacement requirement left only two
plans: equilibrium was often reached before entry and the late market price
destroyed costed R. Candidate 30 changes the execution transition, not the
detector or a numeric threshold.

After the already-completed reclaim bar itself demonstrates opposite flow,
body and close location, a post-only limit is placed at the reclaimed prior
range edge. The observed raid extreme remains the stop and the frozen prior
range midpoint remains the target. A fill can therefore occur only on a later
boundary retest, while NautilusTrader remains the sole owner of fill, fee,
position and NAV accounting.
"""
from __future__ import annotations

from typing import Any

import candidate29_quarter_hour_failed_auction as base
from logic import BarObs, CausalAuctionEngine, Direction, MINUTE_NS, Scenario, Side, TradePlan

SCENARIO_KIND = "QUARTER_HOUR_FAILED_AUCTION_BOUNDARY_RETEST"


def _passive_economics(
    *,
    direction: Direction,
    entry: float,
    stop: float,
    target: float,
    maker_rate: float,
    taker_rate: float,
) -> tuple[float, float, float, float]:
    if direction == Direction.LONG:
        risk = entry - stop
        gross_gain = target - entry
    else:
        risk = stop - entry
        gross_gain = entry - target
    loss = risk + entry * maker_rate + stop * taker_rate
    net_gain = gross_gain - entry * maker_rate - target * maker_rate
    net_r = net_gain / loss if loss > 0.0 else float("-inf")
    return risk, loss, net_gain, net_r


def _reclaim_retest_plan(
    self: CausalAuctionEngine,
    state: base.QuarterHourFailedAuctionState,
    bar: BarObs,
    atr: float,
) -> TradePlan | None:
    if state.direction == Direction.SHORT:
        entry = state.range_high
        stop = state.sweep_extreme + self.config.stop_buffer_atr * atr
        passive = entry > bar.close
    else:
        entry = state.range_low
        stop = state.sweep_extreme - self.config.stop_buffer_atr * atr
        passive = entry < bar.close
    target = state.range_midpoint
    risk, loss, net_gain, net_r = _passive_economics(
        direction=state.direction,
        entry=entry,
        stop=stop,
        target=target,
        maker_rate=self.config.effective_maker_rate,
        taker_rate=self.config.effective_taker_rate,
    )
    causal_order = (
        stop < entry < target
        if state.direction == Direction.LONG
        else target < entry < stop
    )
    if (
        not passive
        or not causal_order
        or risk <= 0.0
        or risk / atr < self.config.min_stop_atr
        or net_gain <= 0.0
        or net_r < self.config.min_net_r
    ):
        base._terminal(
            self,
            state,
            bar,
            "QUARTER_HOUR_BOUNDARY_RETEST_INSUFFICIENT_COSTED_R",
        )
        return None

    # The order is structurally valid only in the current quarter-hour. The
    # next clock boundary closes the failed-auction episode.
    expire_ts_ns = state.boundary_ts_ns + 15 * MINUTE_NS
    if expire_ts_ns <= bar.ts_ns:
        base._terminal(
            self,
            state,
            bar,
            "QUARTER_HOUR_BOUNDARY_RETEST_EXPIRED_AT_CONFIRMATION",
        )
        return None

    plan = TradePlan(
        scenario_id=state.scenario_id,
        scenario=Scenario.FAR,
        direction=state.direction,
        observed_ts_ns=bar.ts_ns,
        expected_entry=entry,
        stop_price=stop,
        target_price=target,
        atr=atr,
        loss_per_unit=loss,
        gain_per_unit=net_gain,
        net_r=net_r,
        reason_code="QUARTER_HOUR_RANGE_RECLAIM_BOUNDARY_RETEST_LIMIT",
        expire_ts_ns=expire_ts_ns,
        entry_order_type="LIMIT",
        entry_post_only=True,
        details={
            "scenario_kind": SCENARIO_KIND,
            "sweep_ts_ns": state.opening_ts_ns,
            "clock_boundary_ts_ns": state.boundary_ts_ns,
            "swept_side": state.swept_side.value,
            "range_start_ts_ns": state.range_start_ts_ns,
            "range_end_ts_ns": state.range_end_ts_ns,
            "range_high": state.range_high,
            "range_low": state.range_low,
            "range_midpoint": state.range_midpoint,
            "sweep_extreme": state.sweep_extreme,
            "reclaim_ts_ns": bar.ts_ns,
            "entry_model": "POST_RECLAIM_BOUNDARY_RETEST",
            "stop_model": "QUARTER_HOUR_OPENING_RAID_EXTREME",
            "target_model": "PREVIOUS_QUARTER_EQUILIBRIUM",
            "entry_cost_assumption": "MAKER",
            "entry_expiry_clock_boundary_ns": expire_ts_ns,
        },
    )
    self._event(
        state.scenario_id,
        "TRADE_PLAN_CONFIRMED",
        state.opening_ts_ns,
        bar.ts_ns,
        "WAIT_RECLAIM",
        "PLAN_CONFIRMED",
        plan.reason_code,
        entry,
        {
            "scenario": Scenario.FAR.value,
            "scenario_kind": SCENARIO_KIND,
            "direction": state.direction.value,
            "entry_order_type": "LIMIT",
            "entry_post_only": True,
            "expire_ts_ns": expire_ts_ns,
            "stop": stop,
            "target": target,
            "net_r": net_r,
        },
    )
    state.state = "PLAN_CONFIRMED"
    state.reclaim_ts_ns = bar.ts_ns
    state.reclaim_index = self._index
    return plan


def _step(
    self: CausalAuctionEngine,
    state: base.QuarterHourFailedAuctionState,
    bar: BarObs,
    atr: float,
) -> TradePlan | None:
    if self._index > state.expiry_index:
        base._terminal(self, state, bar, "QUARTER_HOUR_BOUNDARY_RETEST_STATE_EXPIRED")
        return None

    target_reached = (
        bar.low <= state.range_midpoint
        if state.direction == Direction.SHORT
        else bar.high >= state.range_midpoint
    )
    if target_reached:
        base._terminal(
            self,
            state,
            bar,
            "QUARTER_HOUR_EQUILIBRIUM_REACHED_BEFORE_RETEST_ORDER",
        )
        return None
    if state.state != "WAIT_RECLAIM":
        return None

    # The opening minute creates the raid context but cannot also confirm its
    # own failure. At least one later completed bar must close back inside.
    if self._index <= state.opening_index:
        return None

    if state.swept_side == Side.HIGH:
        if bar.high > state.sweep_extreme:
            state.sweep_extreme = bar.high
        accepted = (
            bar.close >= state.range_high + self.config.acceptance_close_atr * atr
        )
        reclaimed = (
            bar.close <= state.range_high - self.config.rejection_reclaim_atr * atr
        )
        directional_flow = bar.signed_flow <= -self.config.displacement_flow_min
        directional_location = (
            bar.close_location <= 1.0 - self.config.acceptance_close_location
        )
    else:
        if bar.low < state.sweep_extreme:
            state.sweep_extreme = bar.low
        accepted = (
            bar.close <= state.range_low - self.config.acceptance_close_atr * atr
        )
        reclaimed = (
            bar.close >= state.range_low + self.config.rejection_reclaim_atr * atr
        )
        directional_flow = bar.signed_flow >= self.config.displacement_flow_min
        directional_location = (
            bar.close_location >= self.config.acceptance_close_location
        )

    state.accepted_outside_streak = (
        state.accepted_outside_streak + 1 if accepted else 0
    )
    if state.accepted_outside_streak >= self.config.acceptance_min_closes:
        base._terminal(self, state, bar, "QUARTER_HOUR_SWEEP_ACCEPTED")
        return None

    directional_body = bar.body >= self.config.displacement_body_atr * atr
    if not (
        reclaimed
        and directional_flow
        and directional_location
        and directional_body
    ):
        return None

    self._event(
        state.scenario_id,
        "QUARTER_HOUR_RANGE_RECLAIMED",
        state.opening_ts_ns,
        bar.ts_ns,
        "WAIT_RECLAIM",
        "PLAN_PENDING_COST_GATE",
        "PREVIOUS_QUARTER_EDGE_RECLAIMED_WITH_OPPOSITE_DELIVERY",
        state.range_high if state.swept_side == Side.HIGH else state.range_low,
        {
            "direction": state.direction.value,
            "sweep_extreme": state.sweep_extreme,
            "reclaim_close": bar.close,
            "reclaim_signed_flow": bar.signed_flow,
            "reclaim_body_atr": bar.body / atr,
            "reclaim_close_location": bar.close_location,
        },
    )
    return _reclaim_retest_plan(self, state, bar, atr)


def install() -> None:
    # Candidate 29 supplies the causal detector and lifecycle integration.
    # Replacing its step function changes only the post-detection transition and
    # passive execution contract.
    base.install()
    base.SCENARIO_KIND = SCENARIO_KIND
    base._step = _step
