"""Candidate 31: pre-positioned reclaim stop after a quarter-hour edge raid.

Candidate 29 waited for a later market displacement and usually arrived after
prior-quarter equilibrium or after costed R had collapsed. Candidate 30 waited
for a completed reclaim and then a passive boundary retest. Candidate 31 tests
a different causal execution state: immediately after the completed opening
minute establishes a one-sided raid, place a native STOP_MARKET parent at the
already-known in-range reclaim threshold.

Nothing is entered retroactively. The opening minute must still close on the
swept side of the trigger when the order is submitted. NautilusTrader alone
owns the later trigger, fill, fees, stop, target, position and NAV. The observed
raid extreme remains invalidation and the frozen previous-quarter midpoint
remains target. All numerical thresholds are inherited.
"""
from __future__ import annotations

import candidate29_quarter_hour_failed_auction as base
from logic import BarObs, CausalAuctionEngine, Direction, MINUTE_NS, Scenario, TradePlan

SCENARIO_KIND = "QUARTER_HOUR_FAILED_AUCTION_RECLAIM_STOP"


def _stop_market_economics(
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


def _reclaim_stop_plan(
    self: CausalAuctionEngine,
    state: base.QuarterHourFailedAuctionState,
    bar: BarObs,
    atr: float,
) -> TradePlan | None:
    if self._index != state.opening_index:
        return None

    if state.direction == Direction.SHORT:
        trigger = state.range_high - self.config.rejection_reclaim_atr * atr
        stop = state.sweep_extreme + self.config.stop_buffer_atr * atr
        not_yet_triggered = bar.close > trigger
    else:
        trigger = state.range_low + self.config.rejection_reclaim_atr * atr
        stop = state.sweep_extreme - self.config.stop_buffer_atr * atr
        not_yet_triggered = bar.close < trigger
    target = state.range_midpoint

    # A completed opening bar which already crossed the trigger cannot receive a
    # retroactive entry. That episode belongs to a close-confirmed design.
    if not not_yet_triggered:
        base._terminal(
            self,
            state,
            bar,
            "QUARTER_HOUR_RECLAIM_TRIGGER_ALREADY_CROSSED_AT_SUBMISSION",
        )
        return None

    risk, loss, net_gain, net_r = _stop_market_economics(
        direction=state.direction,
        entry=trigger,
        stop=stop,
        target=target,
        taker_rate=self.config.effective_taker_rate,
        maker_rate=self.config.effective_maker_rate,
    )
    causal_order = (
        stop < trigger < target
        if state.direction == Direction.LONG
        else target < trigger < stop
    )
    if (
        not causal_order
        or risk <= 0.0
        or risk / atr < self.config.min_stop_atr
        or net_gain <= 0.0
        or net_r < self.config.min_net_r
    ):
        base._terminal(
            self,
            state,
            bar,
            "QUARTER_HOUR_RECLAIM_STOP_INSUFFICIENT_COSTED_R",
        )
        return None

    expire_ts_ns = state.boundary_ts_ns + 15 * MINUTE_NS
    if expire_ts_ns <= bar.ts_ns:
        base._terminal(
            self,
            state,
            bar,
            "QUARTER_HOUR_RECLAIM_STOP_EXPIRED_AT_SUBMISSION",
        )
        return None

    plan = TradePlan(
        scenario_id=state.scenario_id,
        scenario=Scenario.FAR,
        direction=state.direction,
        observed_ts_ns=bar.ts_ns,
        expected_entry=trigger,
        stop_price=stop,
        target_price=target,
        atr=atr,
        loss_per_unit=loss,
        gain_per_unit=net_gain,
        net_r=net_r,
        reason_code="QUARTER_HOUR_RANGE_RECLAIM_STOP_MARKET",
        expire_ts_ns=expire_ts_ns,
        entry_order_type="STOP_MARKET",
        entry_post_only=False,
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
            "entry_trigger_price": trigger,
            "entry_model": "PREPOSITIONED_IN_RANGE_RECLAIM_STOP_MARKET",
            "stop_model": "QUARTER_HOUR_OPENING_RAID_EXTREME",
            "target_model": "PREVIOUS_QUARTER_EQUILIBRIUM",
            "entry_cost_assumption": "TAKER",
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
        trigger,
        {
            "scenario": Scenario.FAR.value,
            "scenario_kind": SCENARIO_KIND,
            "direction": state.direction.value,
            "entry_order_type": "STOP_MARKET",
            "entry_post_only": False,
            "entry_trigger_price": trigger,
            "expire_ts_ns": expire_ts_ns,
            "stop": stop,
            "target": target,
            "net_r": net_r,
        },
    )
    state.state = "PLAN_CONFIRMED"
    return plan


def install() -> None:
    # Candidate 29 supplies the frozen prior-quarter detector and all lifecycle
    # integration. Candidate 31 changes only the causal parent-order transition.
    base.install()
    base.SCENARIO_KIND = SCENARIO_KIND
    base._step = _reclaim_stop_plan
