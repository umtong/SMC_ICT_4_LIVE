"""Quarter-hour completed-range failed-auction reversal for Candidate 29.

Candidate 27 and Candidate 28 treated quarter-hour opening activity as
continuation. Their broad 16-week replays failed across every symbol and both
directions. Candidate 29 tests the complementary state suggested by the same
clock-time evidence: the *opening return burst* may be transient even when order
flow contains slower information.

The entry object is therefore not a generic quarter-hour candle. It is a
completed previous fifteen-minute auction followed by a first-minute trade
through one edge, aggressive flow into that edge, completed re-entry into the
old range, and a later displacement away from the raid. The frozen previous
range midpoint is the equilibrium target and the observed raid extreme is the
invalidation. All thresholds, costs, exact current-NAV risk and portfolio
mutexes are inherited unchanged.
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

SCENARIO_KIND = "QUARTER_HOUR_PREVIOUS_RANGE_FAILED_AUCTION"
_FIRST_MINUTE_CLOSES = frozenset({1, 16, 31, 46})


@dataclass(slots=True)
class QuarterHourFailedAuctionState:
    scenario_id: str
    swept_side: Side
    direction: Direction
    boundary_ts_ns: int
    opening_ts_ns: int
    opening_index: int
    expiry_index: int
    range_start_ts_ns: int
    range_end_ts_ns: int
    range_high: float
    range_low: float
    range_midpoint: float
    sweep_extreme: float
    episode_break_level: float
    opening_signed_flow: float
    opening_relative_volume: float
    state: str = "WAIT_RECLAIM"
    accepted_outside_streak: int = 0
    reclaim_ts_ns: int | None = None
    reclaim_index: int | None = None


def _is_first_completed_minute_after_quarter(ts_ns: int) -> bool:
    if ts_ns < 0:
        return False
    return int((ts_ns // MINUTE_NS) % 60) in _FIRST_MINUTE_CLOSES


def _completed_previous_quarter_range(
    self: CausalAuctionEngine,
    opening_ts_ns: int,
) -> tuple[int, int, float, float, float] | None:
    completed = [
        bar
        for bar in self.internal_bars
        if int(bar.end_ts_ns) < opening_ts_ns
    ]
    if len(completed) < 3:
        return None
    sample = completed[-3:]
    if any(bar.high <= bar.low for bar in sample):
        return None
    start = int(sample[0].start_ts_ns)
    end = int(sample[-1].end_ts_ns)
    # The source must be a compact, already-completed quarter-hour auction.
    if end - start > 16 * MINUTE_NS:
        return None
    high = max(float(bar.high) for bar in sample)
    low = min(float(bar.low) for bar in sample)
    if high <= low:
        return None
    return start, end, high, low, (high + low) / 2.0


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


def _terminal(
    self: CausalAuctionEngine,
    state: QuarterHourFailedAuctionState,
    bar: BarObs,
    reason: str,
) -> None:
    self._event(
        state.scenario_id,
        "QUARTER_HOUR_FAILED_AUCTION_TERMINAL",
        state.opening_ts_ns,
        bar.ts_ns,
        state.state,
        "TERMINAL",
        reason,
        state.range_high if state.swept_side == Side.HIGH else state.range_low,
        {
            "swept_side": state.swept_side.value,
            "direction": state.direction.value,
            "range_high": state.range_high,
            "range_low": state.range_low,
            "range_midpoint": state.range_midpoint,
            "sweep_extreme": state.sweep_extreme,
            "episode_break_level": state.episode_break_level,
        },
    )
    self.skips[reason] += 1
    self._candidate29_quarter_hour_failed_state = None


def _detect(
    self: CausalAuctionEngine,
    bar: BarObs,
    atr: float,
) -> None:
    if getattr(self, "_candidate29_quarter_hour_failed_state", None) is not None:
        return
    if self.active_trade_id is not None:
        return
    if not _is_first_completed_minute_after_quarter(bar.ts_ns):
        return

    source = _completed_previous_quarter_range(self, bar.ts_ns)
    if source is None:
        self.skips["QUARTER_HOUR_PREVIOUS_RANGE_NOT_CAUSALLY_COMPLETE"] += 1
        return
    start_ts, end_ts, range_high, range_low, midpoint = source

    median_volume = self.median_volume
    if median_volume is None or median_volume <= 0.0:
        return
    relative_volume = bar.volume / median_volume
    if relative_volume < self.config.min_relative_volume:
        return

    swept_high = bar.high > range_high
    swept_low = bar.low < range_low
    if swept_high == swept_low:
        if swept_high:
            self.skips["QUARTER_HOUR_BOTH_RANGE_SIDES_TRADED_THROUGH"] += 1
        return

    if swept_high:
        penetration = (bar.high - range_high) / atr
        flow_ok = bar.signed_flow >= self.config.absorption_flow_min
        swept_side = Side.HIGH
        direction = Direction.SHORT
        sweep_extreme = bar.high
        episode_break_level = bar.low
    else:
        penetration = (range_low - bar.low) / atr
        flow_ok = bar.signed_flow <= -self.config.absorption_flow_min
        swept_side = Side.LOW
        direction = Direction.LONG
        sweep_extreme = bar.low
        episode_break_level = bar.high
    tolerance = 1e-12
    if not (
        self.config.sweep_min_atr - tolerance
        <= penetration
        <= self.config.sweep_max_atr + tolerance
    ):
        return
    if not flow_ok:
        self.skips["QUARTER_HOUR_SWEEP_WITHOUT_AGGRESSOR_FLOW"] += 1
        return

    target_reached = (
        bar.low <= midpoint if direction == Direction.SHORT else bar.high >= midpoint
    )
    if target_reached:
        self.skips["QUARTER_HOUR_EQUILIBRIUM_REACHED_INSIDE_OPENING_MINUTE"] += 1
        return

    boundary_ts_ns = bar.ts_ns - MINUTE_NS
    scenario_id = (
        f"{self.instrument_id}-QHF-{boundary_ts_ns}-{bar.ts_ns}-{direction.value}"
    )
    state = QuarterHourFailedAuctionState(
        scenario_id=scenario_id,
        swept_side=swept_side,
        direction=direction,
        boundary_ts_ns=boundary_ts_ns,
        opening_ts_ns=bar.ts_ns,
        opening_index=self._index,
        expiry_index=self._index + self.config.retrace_expiry_bars,
        range_start_ts_ns=start_ts,
        range_end_ts_ns=end_ts,
        range_high=range_high,
        range_low=range_low,
        range_midpoint=midpoint,
        sweep_extreme=sweep_extreme,
        episode_break_level=episode_break_level,
        opening_signed_flow=bar.signed_flow,
        opening_relative_volume=relative_volume,
    )
    self._candidate29_quarter_hour_failed_state = state
    self._event(
        scenario_id,
        "QUARTER_HOUR_PREVIOUS_RANGE_SWEEP",
        boundary_ts_ns,
        bar.ts_ns,
        "PREVIOUS_QUARTER_COMPLETE",
        "WAIT_RECLAIM",
        "FIRST_MINUTE_EXTERNAL_RANGE_TRADE_THROUGH",
        range_high if swept_side == Side.HIGH else range_low,
        {
            "swept_side": swept_side.value,
            "direction": direction.value,
            "range_start_ts_ns": start_ts,
            "range_end_ts_ns": end_ts,
            "range_high": range_high,
            "range_low": range_low,
            "range_midpoint": midpoint,
            "penetration_atr": penetration,
            "opening_signed_flow": bar.signed_flow,
            "opening_relative_volume": relative_volume,
            "sweep_extreme": sweep_extreme,
        },
    )


def _plan(
    self: CausalAuctionEngine,
    state: QuarterHourFailedAuctionState,
    bar: BarObs,
    atr: float,
) -> TradePlan | None:
    stop = (
        state.sweep_extreme - self.config.stop_buffer_atr * atr
        if state.direction == Direction.LONG
        else state.sweep_extreme + self.config.stop_buffer_atr * atr
    )
    entry = bar.close
    risk, loss, net_gain, net_r = _market_economics(
        direction=state.direction,
        entry=entry,
        stop=stop,
        target=state.range_midpoint,
        taker_rate=self.config.effective_taker_rate,
        maker_rate=self.config.effective_maker_rate,
    )
    causal_order = (
        stop < entry < state.range_midpoint
        if state.direction == Direction.LONG
        else state.range_midpoint < entry < stop
    )
    if (
        not causal_order
        or risk <= 0.0
        or risk / atr < self.config.min_stop_atr
        or net_gain <= 0.0
        or net_r < self.config.min_net_r
    ):
        _terminal(
            self,
            state,
            bar,
            "QUARTER_HOUR_FAILED_AUCTION_INSUFFICIENT_COSTED_R",
        )
        return None

    plan = TradePlan(
        scenario_id=state.scenario_id,
        scenario=Scenario.FAR,
        direction=state.direction,
        observed_ts_ns=bar.ts_ns,
        expected_entry=entry,
        stop_price=stop,
        target_price=state.range_midpoint,
        atr=atr,
        loss_per_unit=loss,
        gain_per_unit=net_gain,
        net_r=net_r,
        reason_code="QUARTER_HOUR_RANGE_SWEEP_RECLAIM_DISPLACEMENT_MARKET",
        expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,
        entry_order_type="MARKET",
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
            "reclaim_ts_ns": state.reclaim_ts_ns,
            "episode_break_level": state.episode_break_level,
            "opening_signed_flow": state.opening_signed_flow,
            "opening_relative_volume": state.opening_relative_volume,
            "entry_model": "LATER_COMPLETED_RECLAIM_AND_DISPLACEMENT",
            "stop_model": "QUARTER_HOUR_OPENING_RAID_EXTREME",
            "target_model": "PREVIOUS_QUARTER_EQUILIBRIUM",
            "entry_cost_assumption": "TAKER",
            "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,
        },
    )
    self._event(
        state.scenario_id,
        "TRADE_PLAN_CONFIRMED",
        state.opening_ts_ns,
        bar.ts_ns,
        "WAIT_DISPLACEMENT",
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
            "target": state.range_midpoint,
            "net_r": net_r,
        },
    )
    state.state = "PLAN_CONFIRMED"
    return plan


def _step(
    self: CausalAuctionEngine,
    state: QuarterHourFailedAuctionState,
    bar: BarObs,
    atr: float,
) -> TradePlan | None:
    if self._index > state.expiry_index:
        _terminal(self, state, bar, "QUARTER_HOUR_FAILED_AUCTION_EXPIRED")
        return None

    target_reached = (
        bar.low <= state.range_midpoint
        if state.direction == Direction.SHORT
        else bar.high >= state.range_midpoint
    )
    if target_reached:
        _terminal(
            self,
            state,
            bar,
            "QUARTER_HOUR_EQUILIBRIUM_REACHED_BEFORE_ENTRY",
        )
        return None

    if state.state == "WAIT_RECLAIM":
        if state.swept_side == Side.HIGH:
            if bar.high > state.sweep_extreme:
                state.sweep_extreme = bar.high
                state.episode_break_level = min(state.episode_break_level, bar.low)
            accepted = (
                bar.close
                >= state.range_high + self.config.acceptance_close_atr * atr
            )
            reclaimed = (
                bar.close
                <= state.range_high - self.config.rejection_reclaim_atr * atr
            )
        else:
            if bar.low < state.sweep_extreme:
                state.sweep_extreme = bar.low
                state.episode_break_level = max(state.episode_break_level, bar.high)
            accepted = (
                bar.close
                <= state.range_low - self.config.acceptance_close_atr * atr
            )
            reclaimed = (
                bar.close
                >= state.range_low + self.config.rejection_reclaim_atr * atr
            )
        state.accepted_outside_streak = (
            state.accepted_outside_streak + 1 if accepted else 0
        )
        if state.accepted_outside_streak >= self.config.acceptance_min_closes:
            _terminal(self, state, bar, "QUARTER_HOUR_SWEEP_ACCEPTED")
            return None
        if reclaimed:
            state.state = "WAIT_DISPLACEMENT"
            state.reclaim_ts_ns = bar.ts_ns
            state.reclaim_index = self._index
            self._event(
                state.scenario_id,
                "QUARTER_HOUR_RANGE_RECLAIMED",
                state.opening_ts_ns,
                bar.ts_ns,
                "WAIT_RECLAIM",
                "WAIT_DISPLACEMENT",
                "PREVIOUS_QUARTER_EDGE_RECLAIMED",
                state.range_high if state.swept_side == Side.HIGH else state.range_low,
                {
                    "direction": state.direction.value,
                    "sweep_extreme": state.sweep_extreme,
                    "episode_break_level": state.episode_break_level,
                },
            )
        return None

    if state.state != "WAIT_DISPLACEMENT":
        return None

    extended = (
        bar.high > state.sweep_extreme
        if state.swept_side == Side.HIGH
        else bar.low < state.sweep_extreme
    )
    if extended:
        state.sweep_extreme = bar.high if state.swept_side == Side.HIGH else bar.low
        state.episode_break_level = (
            min(state.episode_break_level, bar.low)
            if state.swept_side == Side.HIGH
            else max(state.episode_break_level, bar.high)
        )
        state.state = "WAIT_RECLAIM"
        state.reclaim_ts_ns = None
        state.reclaim_index = None
        state.accepted_outside_streak = 0
        self._event(
            state.scenario_id,
            "QUARTER_HOUR_SWEEP_EXTENDED",
            state.opening_ts_ns,
            bar.ts_ns,
            "WAIT_DISPLACEMENT",
            "WAIT_RECLAIM",
            "FRESH_OPENING_RAID_REQUIRES_NEW_RECLAIM",
            state.sweep_extreme,
        )
        return None
    if state.reclaim_index is None or self._index <= state.reclaim_index:
        return None

    if state.direction == Direction.SHORT:
        broke = bar.close < state.episode_break_level
        flow = bar.signed_flow <= -self.config.displacement_flow_min
        location = bar.close_location <= 1.0 - self.config.acceptance_close_location
    else:
        broke = bar.close > state.episode_break_level
        flow = bar.signed_flow >= self.config.displacement_flow_min
        location = bar.close_location >= self.config.acceptance_close_location
    body = bar.body >= self.config.displacement_body_atr * atr
    if not (broke and flow and location and body):
        return None
    return _plan(self, state, bar, atr)


BASE_ON_BAR: Callable[..., TradePlan | None] | None = None
BASE_MARK_SUBMITTED: Callable[..., None] | None = None
BASE_MARK_REJECTED: Callable[..., None] | None = None
BASE_MARK_TRADE_TERMINAL: Callable[..., None] | None = None


def candidate29_on_bar(
    self: CausalAuctionEngine,
    bar: BarObs,
    *,
    allow_entry: bool = True,
) -> TradePlan | None:
    if BASE_ON_BAR is None:
        raise RuntimeError("Candidate 29 is not installed")
    base_plan = BASE_ON_BAR(self, bar, allow_entry=allow_entry)
    if base_plan is not None:
        return base_plan
    if self.active_trade_id is not None:
        return None
    if getattr(self, "_candidate16_failed_far_state", None) is not None:
        return None

    atr = self.atr
    if atr is None or atr <= 0.0:
        return None
    state: QuarterHourFailedAuctionState | None = getattr(
        self,
        "_candidate29_quarter_hour_failed_state",
        None,
    )
    if state is None:
        _detect(self, bar, atr)
        state = getattr(self, "_candidate29_quarter_hour_failed_state", None)
        if state is None:
            return None

    plan = _step(self, state, bar, atr)
    if plan is not None and not allow_entry:
        candidate29_mark_rejected(
            self,
            plan,
            bar.ts_ns,
            "OUTSIDE_EVALUATION_WINDOW",
        )
        return None
    return plan


def candidate29_mark_submitted(
    self: CausalAuctionEngine,
    plan: TradePlan,
    quantity: Any,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_SUBMITTED is None:
        raise RuntimeError("Candidate 29 is not installed")
    state: QuarterHourFailedAuctionState | None = getattr(
        self,
        "_candidate29_quarter_hour_failed_state",
        None,
    )
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        if (
            state is None
            or state.scenario_id != plan.scenario_id
            or state.state != "PLAN_CONFIRMED"
        ):
            raise RuntimeError("submitted quarter-hour FAR does not match pending state")
        if self.active_trade_id is not None:
            raise RuntimeError("global candidate slot already occupied")
        if self.active is not None and self.bars:
            self._terminal(
                self.active,
                self.bars[-1],
                "QUARTER_HOUR_FAILED_AUCTION_ALLOCATED_BEFORE_EXTERNAL_PLAN",
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
        self._candidate29_trade_kind = SCENARIO_KIND
        self._candidate29_quarter_hour_failed_state = None
        self._candidate16_trade_kind = "OTHER"
        self._candidate16_submitted_far = None
        return

    if state is not None and self.bars:
        _terminal(self, state, self.bars[-1], "COMPETING_EXTERNAL_PLAN_ALLOCATED")
    BASE_MARK_SUBMITTED(self, plan, quantity, details)


def candidate29_mark_rejected(
    self: CausalAuctionEngine,
    plan: TradePlan,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_REJECTED is None:
        raise RuntimeError("Candidate 29 is not installed")
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        state: QuarterHourFailedAuctionState | None = getattr(
            self,
            "_candidate29_quarter_hour_failed_state",
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
        self._candidate29_quarter_hour_failed_state = None
        return
    BASE_MARK_REJECTED(self, plan, ts_ns, reason, details)


def candidate29_mark_trade_terminal(
    self: CausalAuctionEngine,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_TRADE_TERMINAL is None:
        raise RuntimeError("Candidate 29 is not installed")
    BASE_MARK_TRADE_TERMINAL(self, ts_ns, reason, details)
    self._candidate29_trade_kind = None


def install() -> None:
    global BASE_ON_BAR, BASE_MARK_SUBMITTED, BASE_MARK_REJECTED, BASE_MARK_TRADE_TERMINAL
    if CausalAuctionEngine.on_bar is candidate29_on_bar:
        return
    BASE_ON_BAR = CausalAuctionEngine.on_bar
    BASE_MARK_SUBMITTED = CausalAuctionEngine.mark_submitted
    BASE_MARK_REJECTED = CausalAuctionEngine.mark_rejected
    BASE_MARK_TRADE_TERMINAL = CausalAuctionEngine.mark_trade_terminal
    CausalAuctionEngine.on_bar = candidate29_on_bar
    CausalAuctionEngine.mark_submitted = candidate29_mark_submitted
    CausalAuctionEngine.mark_rejected = candidate29_mark_rejected
    CausalAuctionEngine.mark_trade_terminal = candidate29_mark_trade_terminal
