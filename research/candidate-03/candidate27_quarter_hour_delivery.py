"""Quarter-hour synchronized delivery scenario for Candidate 27.

The scenario addresses Candidate 26's dominant failure: a generic internal
liquidity raid/reclaim frequently confused persistent price discovery with
absorption. Candidate 27 changes the *state source*, not a numeric threshold.

A state may begin only on the first fully completed one-minute bar after a UTC
quarter-hour boundary. That bar must show directional body, close location,
relative activity and taker-flow imbalance using already frozen controls. A
later completed bar must accept and extend beyond the opening-bar extreme.
Only then is a native market parent proposed toward a pre-existing external
liquidity pool.

The opening bar supplies clock-conditioned context. The later bar supplies the
transition confirmation. The external pool supplies the objective. These roles
are deliberately separate. NautilusTrader remains the sole owner of orders,
fills, fees, positions and account NAV.
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

SCENARIO_KIND = "QUARTER_HOUR_SYNCHRONIZED_DELIVERY"
_FIRST_MINUTE_CLOSES = frozenset({1, 16, 31, 46})


@dataclass(slots=True)
class QuarterHourState:
    scenario_id: str
    direction: Direction
    boundary_ts_ns: int
    opening_ts_ns: int
    opening_index: int
    expiry_index: int
    opening_open: float
    opening_high: float
    opening_low: float
    opening_close: float
    pullback_extreme: float
    target_pool_id: str
    target_price: float
    target_source: str
    target_strength: int
    opening_signed_flow: float
    opening_relative_volume: float
    adverse_close_streak: int = 0
    state: str = "WAIT_CONFIRMATION"


def _is_first_completed_minute_after_quarter(ts_ns: int) -> bool:
    if ts_ns < 0:
        return False
    utc_minute = (ts_ns // MINUTE_NS) % 60
    return int(utc_minute) in _FIRST_MINUTE_CLOSES


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


def _strict_external_target(
    self: CausalAuctionEngine,
    direction: Direction,
    reference: float,
):
    side = Side.HIGH if direction == Direction.LONG else Side.LOW
    candidates = [
        pool
        for pool in self.pools
        if not pool.consumed
        and pool.external
        and pool.confirmed_index < self._index
        and self._index <= pool.expiry_index
        and pool.source != "ROUND_NUMBER"
        and "SHELF" not in pool.source
        and (
            (side == Side.HIGH and pool.side == Side.HIGH and pool.level > reference)
            or (side == Side.LOW and pool.side == Side.LOW and pool.level < reference)
        )
    ]
    return min(candidates, key=lambda pool: abs(pool.level - reference)) if candidates else None


def _target_is_live(self: CausalAuctionEngine, state: QuarterHourState) -> bool:
    return any(
        pool.scenario_id == state.target_pool_id
        and not pool.consumed
        and self._index <= pool.expiry_index
        for pool in self.pools
    )


def _terminal(
    self: CausalAuctionEngine,
    state: QuarterHourState,
    bar: BarObs,
    reason: str,
) -> None:
    self._event(
        state.scenario_id,
        "QUARTER_HOUR_DELIVERY_TERMINAL",
        state.opening_ts_ns,
        bar.ts_ns,
        state.state,
        "TERMINAL",
        reason,
        state.opening_close,
        {
            "direction": state.direction.value,
            "boundary_ts_ns": state.boundary_ts_ns,
            "opening_high": state.opening_high,
            "opening_low": state.opening_low,
            "target_pool": state.target_pool_id,
            "target": state.target_price,
            "target_source": state.target_source,
            "pullback_extreme": state.pullback_extreme,
        },
    )
    self.skips[reason] += 1
    self._candidate27_quarter_hour_state = None


def _detect(
    self: CausalAuctionEngine,
    bar: BarObs,
    atr: float,
) -> None:
    if getattr(self, "_candidate27_quarter_hour_state", None) is not None:
        return
    if self.active_trade_id is not None or not _is_first_completed_minute_after_quarter(bar.ts_ns):
        return

    median_volume = self.median_volume
    if median_volume is None or median_volume <= 0.0:
        return
    relative_volume = bar.volume / median_volume
    if relative_volume < self.config.min_relative_volume:
        return
    if bar.body < self.config.displacement_body_atr * atr:
        return

    long_opening = (
        bar.close > bar.open
        and bar.signed_flow >= self.config.acceptance_flow_min
        and bar.close_location >= self.config.acceptance_close_location
    )
    short_opening = (
        bar.close < bar.open
        and bar.signed_flow <= -self.config.acceptance_flow_min
        and bar.close_location <= 1.0 - self.config.acceptance_close_location
    )
    if long_opening == short_opening:
        return
    direction = Direction.LONG if long_opening else Direction.SHORT

    target = _strict_external_target(self, direction, bar.close)
    if target is None:
        self.skips["QUARTER_HOUR_NO_PREEXISTING_EXTERNAL_TARGET"] += 1
        return
    target_consumed_inside_opening = (
        bar.high >= target.level
        if direction == Direction.LONG
        else bar.low <= target.level
    )
    if target_consumed_inside_opening:
        self.skips["QUARTER_HOUR_TARGET_REACHED_IN_OPENING_MINUTE"] += 1
        return

    boundary_ts_ns = bar.ts_ns - MINUTE_NS
    scenario_id = (
        f"{self.instrument_id}-QHD-{boundary_ts_ns}-{bar.ts_ns}-{direction.value}"
    )
    state = QuarterHourState(
        scenario_id=scenario_id,
        direction=direction,
        boundary_ts_ns=boundary_ts_ns,
        opening_ts_ns=bar.ts_ns,
        opening_index=self._index,
        expiry_index=self._index + self.config.retrace_expiry_bars,
        opening_open=bar.open,
        opening_high=bar.high,
        opening_low=bar.low,
        opening_close=bar.close,
        pullback_extreme=bar.low if direction == Direction.LONG else bar.high,
        target_pool_id=target.scenario_id,
        target_price=float(target.level),
        target_source=str(target.source),
        target_strength=int(target.strength),
        opening_signed_flow=bar.signed_flow,
        opening_relative_volume=relative_volume,
    )
    self._candidate27_quarter_hour_state = state
    self._event(
        scenario_id,
        "QUARTER_HOUR_OPENING_IMBALANCE",
        boundary_ts_ns,
        bar.ts_ns,
        "CLOCK_BOUNDARY",
        "WAIT_CONFIRMATION",
        "FIRST_COMPLETED_MINUTE_DIRECTIONAL_DELIVERY",
        bar.close,
        {
            "direction": direction.value,
            "boundary_ts_ns": boundary_ts_ns,
            "opening_open": bar.open,
            "opening_high": bar.high,
            "opening_low": bar.low,
            "opening_close": bar.close,
            "opening_body_atr": bar.body / atr,
            "opening_signed_flow": bar.signed_flow,
            "opening_relative_volume": relative_volume,
            "target_pool": target.scenario_id,
            "target": target.level,
            "target_source": target.source,
            "target_strength": target.strength,
        },
    )


def _plan(
    self: CausalAuctionEngine,
    state: QuarterHourState,
    bar: BarObs,
    atr: float,
) -> TradePlan | None:
    stop = (
        state.pullback_extreme - self.config.stop_buffer_atr * atr
        if state.direction == Direction.LONG
        else state.pullback_extreme + self.config.stop_buffer_atr * atr
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
        _terminal(self, state, bar, "QUARTER_HOUR_DELIVERY_INSUFFICIENT_COSTED_R")
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
        reason_code="QUARTER_HOUR_OPENING_HOLD_REACCELERATION_MARKET",
        expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,
        entry_order_type="MARKET",
        entry_post_only=False,
        details={
            "scenario_kind": SCENARIO_KIND,
            "sweep_ts_ns": state.opening_ts_ns,
            "clock_boundary_ts_ns": state.boundary_ts_ns,
            "opening_ts_ns": state.opening_ts_ns,
            "opening_open": state.opening_open,
            "opening_high": state.opening_high,
            "opening_low": state.opening_low,
            "opening_close": state.opening_close,
            "opening_signed_flow": state.opening_signed_flow,
            "opening_relative_volume": state.opening_relative_volume,
            "pullback_extreme": state.pullback_extreme,
            "target_pool": state.target_pool_id,
            "target_source": state.target_source,
            "target_strength": state.target_strength,
            "entry_model": "LATER_COMPLETED_OPENING_EXTREME_REACCELERATION",
            "stop_model": "QUARTER_HOUR_OPENING_LEG_INVALIDATION",
            "entry_cost_assumption": "TAKER",
            "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,
        },
    )
    self._event(
        state.scenario_id,
        "TRADE_PLAN_CONFIRMED",
        state.opening_ts_ns,
        bar.ts_ns,
        "WAIT_CONFIRMATION",
        "PLAN_CONFIRMED",
        plan.reason_code,
        entry,
        {
            "scenario": Scenario.AAC.value,
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
    state: QuarterHourState,
    bar: BarObs,
    atr: float,
) -> TradePlan | None:
    if self._index > state.expiry_index:
        _terminal(self, state, bar, "QUARTER_HOUR_DELIVERY_EXPIRED")
        return None
    if not _target_is_live(self, state):
        _terminal(self, state, bar, "QUARTER_HOUR_EXTERNAL_TARGET_NO_LONGER_LIVE")
        return None
    target_reached = (
        bar.high >= state.target_price
        if state.direction == Direction.LONG
        else bar.low <= state.target_price
    )
    if target_reached:
        _terminal(self, state, bar, "QUARTER_HOUR_TARGET_REACHED_BEFORE_ENTRY")
        return None

    if state.direction == Direction.LONG:
        state.pullback_extreme = min(state.pullback_extreme, bar.low)
        deeply_failed = (
            bar.close
            <= state.opening_low - self.config.acceptance_retest_atr * atr
        )
        adverse_close = (
            bar.close
            <= state.opening_open - self.config.acceptance_hold_atr * atr
        )
    else:
        state.pullback_extreme = max(state.pullback_extreme, bar.high)
        deeply_failed = (
            bar.close
            >= state.opening_high + self.config.acceptance_retest_atr * atr
        )
        adverse_close = (
            bar.close
            >= state.opening_open + self.config.acceptance_hold_atr * atr
        )
    if deeply_failed:
        _terminal(self, state, bar, "QUARTER_HOUR_OPENING_LEG_INVALIDATED")
        return None
    state.adverse_close_streak = (
        state.adverse_close_streak + 1 if adverse_close else 0
    )
    if state.adverse_close_streak >= self.config.acceptance_min_closes:
        _terminal(self, state, bar, "QUARTER_HOUR_OPENING_ACCEPTANCE_FAILED")
        return None
    if self._index <= state.opening_index:
        return None

    if state.direction == Direction.LONG:
        extended = bar.close > state.opening_high
        flow = bar.signed_flow >= self.config.reacceleration_flow_min
        location = bar.close_location >= self.config.acceptance_close_location
    else:
        extended = bar.close < state.opening_low
        flow = bar.signed_flow <= -self.config.reacceleration_flow_min
        location = bar.close_location <= 1.0 - self.config.acceptance_close_location
    body = bar.body >= self.config.reacceleration_body_atr * atr
    if not (extended and flow and location and body):
        return None
    return _plan(self, state, bar, atr)


BASE_ON_BAR: Callable[..., TradePlan | None] | None = None
BASE_MARK_SUBMITTED: Callable[..., None] | None = None
BASE_MARK_REJECTED: Callable[..., None] | None = None
BASE_MARK_TRADE_TERMINAL: Callable[..., None] | None = None


def candidate27_on_bar(
    self: CausalAuctionEngine,
    bar: BarObs,
    *,
    allow_entry: bool = True,
) -> TradePlan | None:
    if BASE_ON_BAR is None:
        raise RuntimeError("Candidate 27 is not installed")
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
    state: QuarterHourState | None = getattr(
        self,
        "_candidate27_quarter_hour_state",
        None,
    )
    if state is None:
        _detect(self, bar, atr)
        return None

    plan = _step(self, state, bar, atr)
    if plan is not None and not allow_entry:
        candidate27_mark_rejected(
            self,
            plan,
            bar.ts_ns,
            "OUTSIDE_EVALUATION_WINDOW",
        )
        return None
    return plan


def candidate27_mark_submitted(
    self: CausalAuctionEngine,
    plan: TradePlan,
    quantity: Any,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_SUBMITTED is None:
        raise RuntimeError("Candidate 27 is not installed")
    state: QuarterHourState | None = getattr(
        self,
        "_candidate27_quarter_hour_state",
        None,
    )
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        if (
            state is None
            or state.scenario_id != plan.scenario_id
            or state.state != "PLAN_CONFIRMED"
        ):
            raise RuntimeError("submitted quarter-hour plan does not match pending state")
        if self.active_trade_id is not None:
            raise RuntimeError("global candidate slot already occupied")
        if self.active is not None and self.bars:
            self._terminal(
                self.active,
                self.bars[-1],
                "QUARTER_HOUR_PLAN_ALLOCATED_BEFORE_EXTERNAL_PLAN",
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
        self._candidate27_trade_kind = SCENARIO_KIND
        self._candidate27_quarter_hour_state = None
        self._candidate16_trade_kind = "OTHER"
        self._candidate16_submitted_far = None
        return

    if state is not None and self.bars:
        _terminal(self, state, self.bars[-1], "COMPETING_EXTERNAL_PLAN_ALLOCATED")
    BASE_MARK_SUBMITTED(self, plan, quantity, details)


def candidate27_mark_rejected(
    self: CausalAuctionEngine,
    plan: TradePlan,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_REJECTED is None:
        raise RuntimeError("Candidate 27 is not installed")
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        state: QuarterHourState | None = getattr(
            self,
            "_candidate27_quarter_hour_state",
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
        self._candidate27_quarter_hour_state = None
        return
    BASE_MARK_REJECTED(self, plan, ts_ns, reason, details)


def candidate27_mark_trade_terminal(
    self: CausalAuctionEngine,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_TRADE_TERMINAL is None:
        raise RuntimeError("Candidate 27 is not installed")
    BASE_MARK_TRADE_TERMINAL(self, ts_ns, reason, details)
    self._candidate27_trade_kind = None


def install() -> None:
    global BASE_ON_BAR, BASE_MARK_SUBMITTED, BASE_MARK_REJECTED, BASE_MARK_TRADE_TERMINAL
    if CausalAuctionEngine.on_bar is candidate27_on_bar:
        return
    # Capture after Candidate 16/C25 has installed its lifecycle hooks. Import
    # order therefore cannot bypass post-stop state or native exit attribution.
    BASE_ON_BAR = CausalAuctionEngine.on_bar
    BASE_MARK_SUBMITTED = CausalAuctionEngine.mark_submitted
    BASE_MARK_REJECTED = CausalAuctionEngine.mark_rejected
    BASE_MARK_TRADE_TERMINAL = CausalAuctionEngine.mark_trade_terminal
    CausalAuctionEngine.on_bar = candidate27_on_bar
    CausalAuctionEngine.mark_submitted = candidate27_mark_submitted
    CausalAuctionEngine.mark_rejected = candidate27_mark_rejected
    CausalAuctionEngine.mark_trade_terminal = candidate27_mark_trade_terminal
