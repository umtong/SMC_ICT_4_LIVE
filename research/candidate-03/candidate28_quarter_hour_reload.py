"""Quarter-hour context plus internal-liquidity reload for Candidate 28.

Candidate 27 showed that entering immediately after a clock-boundary opening
imbalance and a later opening-extreme break was structurally wrong: the signal
may describe a broader delivery horizon, but the immediate breakout leg was
often already exhausted. Candidate 28 therefore reuses the useful part only as
context and replaces the entry policy.

Ordered causal sequence:

1. first completed one-minute bar after a UTC quarter-hour boundary establishes
   directional clock-conditioned context;
2. a later completed bar extends beyond that opening extreme, confirming that a
   directional delivery leg exists, but no order is submitted;
3. a previously known five-minute internal pivot formed inside that delivery leg
   is raided by opposite aggressor flow;
4. price reclaims the pivot;
5. a still later completed bar reaccelerates through the raid episode; and
6. the unchanged portfolio AAC semantic gate must approve before NautilusTrader
   receives a market bracket.

The target is frozen pre-existing external liquidity. The stop is beyond the
actual reload raid extreme. Context, transition, entry, invalidation and target
therefore use different observations rather than one candle proving itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from candidate27_quarter_hour_delivery import (
    _is_first_completed_minute_after_quarter,
    _market_economics,
    _strict_external_target,
)
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

SCENARIO_KIND = "QUARTER_HOUR_CONTEXT_INTERNAL_RELOAD"


@dataclass(slots=True)
class QuarterHourReloadState:
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
    opening_signed_flow: float
    opening_relative_volume: float
    target_pool_id: str
    target_price: float
    target_source: str
    target_strength: int
    state: str = "WAIT_EXTENSION"
    adverse_close_streak: int = 0
    extension_ts_ns: int | None = None
    extension_index: int | None = None
    extension_extreme: float | None = None
    pivot_candidate_ts_ns: int | None = None
    pivot_known_ts_ns: int | None = None
    pivot_level: float | None = None
    raid_ts_ns: int | None = None
    raid_index: int | None = None
    raid_extreme: float | None = None
    episode_break_level: float | None = None
    reclaim_ts_ns: int | None = None
    reclaim_index: int | None = None


def _target_is_live(
    self: CausalAuctionEngine,
    state: QuarterHourReloadState,
) -> bool:
    return any(
        pool.scenario_id == state.target_pool_id
        and not pool.consumed
        and self._index <= pool.expiry_index
        for pool in self.pools
    )


def _terminal(
    self: CausalAuctionEngine,
    state: QuarterHourReloadState,
    bar: BarObs,
    reason: str,
) -> None:
    self._event(
        state.scenario_id,
        "QUARTER_HOUR_RELOAD_TERMINAL",
        state.opening_ts_ns,
        bar.ts_ns,
        state.state,
        "TERMINAL",
        reason,
        state.pivot_level if state.pivot_level is not None else state.opening_close,
        {
            "direction": state.direction.value,
            "boundary_ts_ns": state.boundary_ts_ns,
            "opening_ts_ns": state.opening_ts_ns,
            "extension_ts_ns": state.extension_ts_ns,
            "pivot_level": state.pivot_level,
            "raid_ts_ns": state.raid_ts_ns,
            "raid_extreme": state.raid_extreme,
            "target_pool": state.target_pool_id,
            "target": state.target_price,
        },
    )
    self.skips[reason] += 1
    self._candidate28_reload_state = None


def _detect_context(
    self: CausalAuctionEngine,
    bar: BarObs,
    atr: float,
) -> None:
    if getattr(self, "_candidate28_reload_state", None) is not None:
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

    long_context = (
        bar.close > bar.open
        and bar.signed_flow >= self.config.acceptance_flow_min
        and bar.close_location >= self.config.acceptance_close_location
    )
    short_context = (
        bar.close < bar.open
        and bar.signed_flow <= -self.config.acceptance_flow_min
        and bar.close_location <= 1.0 - self.config.acceptance_close_location
    )
    if long_context == short_context:
        return
    direction = Direction.LONG if long_context else Direction.SHORT
    target = _strict_external_target(self, direction, bar.close)
    if target is None:
        self.skips["QUARTER_HOUR_RELOAD_NO_EXTERNAL_TARGET"] += 1
        return
    target_reached = (
        bar.high >= target.level if direction == Direction.LONG else bar.low <= target.level
    )
    if target_reached:
        self.skips["QUARTER_HOUR_RELOAD_TARGET_REACHED_IN_OPENING"] += 1
        return

    boundary_ts_ns = bar.ts_ns - MINUTE_NS
    scenario_id = (
        f"{self.instrument_id}-QHR-{boundary_ts_ns}-{bar.ts_ns}-{direction.value}"
    )
    state = QuarterHourReloadState(
        scenario_id=scenario_id,
        direction=direction,
        boundary_ts_ns=boundary_ts_ns,
        opening_ts_ns=bar.ts_ns,
        opening_index=self._index,
        expiry_index=self._index + self.config.event_expiry_bars,
        opening_open=bar.open,
        opening_high=bar.high,
        opening_low=bar.low,
        opening_close=bar.close,
        opening_signed_flow=bar.signed_flow,
        opening_relative_volume=relative_volume,
        target_pool_id=target.scenario_id,
        target_price=float(target.level),
        target_source=str(target.source),
        target_strength=int(target.strength),
    )
    self._candidate28_reload_state = state
    self._event(
        scenario_id,
        "QUARTER_HOUR_RELOAD_CONTEXT_ARMED",
        boundary_ts_ns,
        bar.ts_ns,
        "CLOCK_BOUNDARY",
        "WAIT_EXTENSION",
        "CLOCK_PHASE_DIRECTIONAL_IMBALANCE_CONTEXT",
        bar.close,
        {
            "direction": direction.value,
            "opening_high": bar.high,
            "opening_low": bar.low,
            "opening_body_atr": bar.body / atr,
            "opening_signed_flow": bar.signed_flow,
            "opening_relative_volume": relative_volume,
            "target_pool": target.scenario_id,
            "target": target.level,
            "target_source": target.source,
        },
    )


def _consumed_reload_pivots(
    self: CausalAuctionEngine,
) -> set[tuple[str, int, int, float]]:
    keys = getattr(self, "_candidate28_consumed_reload_pivots", None)
    if keys is None:
        keys = set()
        self._candidate28_consumed_reload_pivots = keys
    return keys


def _select_reload_pivot(
    self: CausalAuctionEngine,
    state: QuarterHourReloadState,
    bar: BarObs,
    prev: BarObs,
    atr: float,
) -> tuple[int, int, float, float] | None:
    if state.extension_ts_ns is None:
        return None
    side = Side.LOW if state.direction == Direction.LONG else Side.HIGH
    pivots = self.internal_lows if side == Side.LOW else self.internal_highs
    numerical_tolerance = 1e-12
    consumed = _consumed_reload_pivots(self)
    eligible: list[tuple[int, int, float, float]] = []
    for candidate_ts_ns, known_ts_ns, level in pivots:
        key = (
            side.value,
            int(candidate_ts_ns),
            int(known_ts_ns),
            round(float(level), 10),
        )
        if key in consumed:
            continue
        # Entry liquidity must belong to the clock-conditioned delivery leg and
        # be fully known before the raid. Older unrelated pivots are context,
        # not an execution trigger.
        if candidate_ts_ns < state.opening_ts_ns or known_ts_ns >= bar.ts_ns:
            continue
        if state.direction == Direction.LONG:
            inside_leg = state.opening_low < level < prev.close
            crossed = prev.close >= level > bar.low
            penetration = (level - bar.low) / atr
        else:
            inside_leg = prev.close < level < state.opening_high
            crossed = prev.close <= level < bar.high
            penetration = (bar.high - level) / atr
        within = (
            self.config.sweep_min_atr - numerical_tolerance
            <= penetration
            <= self.config.sweep_max_atr + numerical_tolerance
        )
        if inside_leg and crossed and within:
            eligible.append(
                (int(candidate_ts_ns), int(known_ts_ns), float(level), penetration)
            )
    if not eligible:
        return None
    # First reachable resting liquidity defines the reload episode.
    return (
        max(eligible, key=lambda item: item[2])
        if state.direction == Direction.LONG
        else min(eligible, key=lambda item: item[2])
    )


def _confirm_extension(
    self: CausalAuctionEngine,
    state: QuarterHourReloadState,
    bar: BarObs,
    atr: float,
) -> bool:
    if self._index <= state.opening_index:
        return False
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
        return False
    state.state = "WAIT_RAID"
    state.extension_ts_ns = bar.ts_ns
    state.extension_index = self._index
    state.extension_extreme = bar.high if state.direction == Direction.LONG else bar.low
    state.adverse_close_streak = 0
    self._event(
        state.scenario_id,
        "QUARTER_HOUR_DELIVERY_LEG_CONFIRMED",
        state.opening_ts_ns,
        bar.ts_ns,
        "WAIT_EXTENSION",
        "WAIT_RAID",
        "LATER_COMPLETED_OPENING_EXTREME_EXTENSION",
        bar.close,
        {
            "direction": state.direction.value,
            "extension_extreme": state.extension_extreme,
            "target_pool": state.target_pool_id,
            "target": state.target_price,
        },
    )
    return True


def _detect_raid(
    self: CausalAuctionEngine,
    state: QuarterHourReloadState,
    bar: BarObs,
    prev: BarObs,
    atr: float,
) -> bool:
    pivot = _select_reload_pivot(self, state, bar, prev, atr)
    if pivot is None:
        return False
    candidate_ts_ns, known_ts_ns, level, penetration = pivot
    opposite_flow = (
        bar.signed_flow <= -self.config.absorption_flow_min
        if state.direction == Direction.LONG
        else bar.signed_flow >= self.config.absorption_flow_min
    )
    if not opposite_flow:
        self.skips["QUARTER_HOUR_RELOAD_RAID_WITHOUT_OPPOSITE_FLOW"] += 1
        return False

    pivot_side = Side.LOW if state.direction == Direction.LONG else Side.HIGH
    _consumed_reload_pivots(self).add(
        (
            pivot_side.value,
            candidate_ts_ns,
            known_ts_ns,
            round(level, 10),
        )
    )
    state.state = "WAIT_RECLAIM"
    state.pivot_candidate_ts_ns = candidate_ts_ns
    state.pivot_known_ts_ns = known_ts_ns
    state.pivot_level = level
    state.raid_ts_ns = bar.ts_ns
    state.raid_index = self._index
    state.raid_extreme = bar.low if state.direction == Direction.LONG else bar.high
    state.episode_break_level = bar.high if state.direction == Direction.LONG else bar.low
    state.adverse_close_streak = 0
    self._event(
        state.scenario_id,
        "QUARTER_HOUR_INTERNAL_RELOAD_RAID",
        candidate_ts_ns,
        bar.ts_ns,
        "WAIT_RAID",
        "WAIT_RECLAIM",
        "DELIVERY_LEG_INTERNAL_LIQUIDITY_TRADED_THROUGH",
        level,
        {
            "direction": state.direction.value,
            "pivot_known_ts_ns": known_ts_ns,
            "penetration_atr": penetration,
            "opposite_aggressor_flow": bar.signed_flow,
            "raid_extreme": state.raid_extreme,
            "episode_break_level": state.episode_break_level,
            "target_pool": state.target_pool_id,
            "target": state.target_price,
        },
    )
    return True


def _step_reclaim(
    self: CausalAuctionEngine,
    state: QuarterHourReloadState,
    bar: BarObs,
    atr: float,
) -> bool:
    assert state.pivot_level is not None
    assert state.raid_extreme is not None
    assert state.episode_break_level is not None
    if state.direction == Direction.LONG:
        state.raid_extreme = min(state.raid_extreme, bar.low)
        state.episode_break_level = max(state.episode_break_level, bar.high)
        adverse = (
            bar.close
            <= state.pivot_level - self.config.acceptance_close_atr * atr
        )
        reclaimed = (
            bar.close
            >= state.pivot_level + self.config.rejection_reclaim_atr * atr
        )
    else:
        state.raid_extreme = max(state.raid_extreme, bar.high)
        state.episode_break_level = min(state.episode_break_level, bar.low)
        adverse = (
            bar.close
            >= state.pivot_level + self.config.acceptance_close_atr * atr
        )
        reclaimed = (
            bar.close
            <= state.pivot_level - self.config.rejection_reclaim_atr * atr
        )
    state.adverse_close_streak = state.adverse_close_streak + 1 if adverse else 0
    if state.adverse_close_streak >= self.config.acceptance_min_closes:
        _terminal(self, state, bar, "QUARTER_HOUR_RELOAD_ACCEPTED_AGAINST_CONTEXT")
        return False
    if not reclaimed:
        return False
    state.state = "WAIT_REACCELERATION"
    state.reclaim_ts_ns = bar.ts_ns
    state.reclaim_index = self._index
    self._event(
        state.scenario_id,
        "QUARTER_HOUR_INTERNAL_RELOAD_RECLAIMED",
        state.raid_ts_ns or state.opening_ts_ns,
        bar.ts_ns,
        "WAIT_RECLAIM",
        "WAIT_REACCELERATION",
        "INTERNAL_PIVOT_RECLAIMED_AFTER_RAID",
        state.pivot_level,
        {
            "direction": state.direction.value,
            "raid_extreme": state.raid_extreme,
            "episode_break_level": state.episode_break_level,
        },
    )
    return True


def _build_plan(
    self: CausalAuctionEngine,
    state: QuarterHourReloadState,
    bar: BarObs,
    atr: float,
) -> TradePlan | None:
    assert state.raid_extreme is not None
    stop = (
        state.raid_extreme - self.config.stop_buffer_atr * atr
        if state.direction == Direction.LONG
        else state.raid_extreme + self.config.stop_buffer_atr * atr
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
        _terminal(self, state, bar, "QUARTER_HOUR_RELOAD_INSUFFICIENT_COSTED_R")
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
        reason_code="QUARTER_HOUR_CONTEXT_INTERNAL_RAID_RELOAD_MARKET",
        expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,
        entry_order_type="MARKET",
        entry_post_only=False,
        details={
            "scenario_kind": SCENARIO_KIND,
            # The portfolio gate measures the independent reload leg rather
            # than reusing the opening context as its own confirmation.
            "sweep_ts_ns": state.raid_ts_ns,
            "clock_boundary_ts_ns": state.boundary_ts_ns,
            "opening_ts_ns": state.opening_ts_ns,
            "opening_signed_flow": state.opening_signed_flow,
            "opening_relative_volume": state.opening_relative_volume,
            "extension_ts_ns": state.extension_ts_ns,
            "pivot_candidate_ts_ns": state.pivot_candidate_ts_ns,
            "pivot_known_ts_ns": state.pivot_known_ts_ns,
            "pivot_level": state.pivot_level,
            "raid_ts_ns": state.raid_ts_ns,
            "raid_extreme": state.raid_extreme,
            "reclaim_ts_ns": state.reclaim_ts_ns,
            "episode_break_level": state.episode_break_level,
            "target_pool": state.target_pool_id,
            "target_source": state.target_source,
            "target_strength": state.target_strength,
            "entry_model": "CLOCK_CONTEXT_INTERNAL_RAID_RECLAIM_REACCELERATION",
            "stop_model": "INTERNAL_RELOAD_RAID_EXTREME_INVALIDATION",
            "entry_cost_assumption": "TAKER",
            "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,
        },
    )
    self._event(
        state.scenario_id,
        "TRADE_PLAN_CONFIRMED",
        state.raid_ts_ns or state.opening_ts_ns,
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
            "stop": stop,
            "target": state.target_price,
            "net_r": net_r,
        },
    )
    state.state = "PLAN_CONFIRMED"
    return plan


def _step_reacceleration(
    self: CausalAuctionEngine,
    state: QuarterHourReloadState,
    bar: BarObs,
    atr: float,
) -> TradePlan | None:
    assert state.pivot_level is not None
    assert state.raid_extreme is not None
    assert state.episode_break_level is not None
    if state.reclaim_index is None or self._index <= state.reclaim_index:
        return None
    if state.direction == Direction.LONG:
        deep_reentry = (
            bar.close
            < state.pivot_level - self.config.acceptance_retest_atr * atr
        )
        reaccelerated = bar.close > state.episode_break_level
        flow = bar.signed_flow >= self.config.reacceleration_flow_min
        location = bar.close_location >= self.config.acceptance_close_location
    else:
        deep_reentry = (
            bar.close
            > state.pivot_level + self.config.acceptance_retest_atr * atr
        )
        reaccelerated = bar.close < state.episode_break_level
        flow = bar.signed_flow <= -self.config.reacceleration_flow_min
        location = bar.close_location <= 1.0 - self.config.acceptance_close_location
    if deep_reentry:
        _terminal(self, state, bar, "QUARTER_HOUR_RELOAD_DEEP_REENTRY")
        return None
    body = bar.body >= self.config.reacceleration_body_atr * atr
    if not (reaccelerated and flow and location and body):
        return None
    return _build_plan(self, state, bar, atr)


def _step(
    self: CausalAuctionEngine,
    state: QuarterHourReloadState,
    bar: BarObs,
    prev: BarObs,
    atr: float,
) -> TradePlan | None:
    if self._index > state.expiry_index:
        _terminal(self, state, bar, "QUARTER_HOUR_RELOAD_EXPIRED")
        return None
    if not _target_is_live(self, state):
        _terminal(self, state, bar, "QUARTER_HOUR_RELOAD_TARGET_NO_LONGER_LIVE")
        return None
    target_reached = (
        bar.high >= state.target_price
        if state.direction == Direction.LONG
        else bar.low <= state.target_price
    )
    if target_reached:
        _terminal(self, state, bar, "QUARTER_HOUR_RELOAD_TARGET_REACHED_BEFORE_ENTRY")
        return None

    if state.state == "WAIT_EXTENSION":
        if state.direction == Direction.LONG:
            deeply_failed = (
                bar.close
                <= state.opening_low - self.config.acceptance_retest_atr * atr
            )
            adverse = (
                bar.close
                <= state.opening_open - self.config.acceptance_hold_atr * atr
            )
        else:
            deeply_failed = (
                bar.close
                >= state.opening_high + self.config.acceptance_retest_atr * atr
            )
            adverse = (
                bar.close
                >= state.opening_open + self.config.acceptance_hold_atr * atr
            )
        if deeply_failed:
            _terminal(self, state, bar, "QUARTER_HOUR_RELOAD_CONTEXT_INVALIDATED")
            return None
        state.adverse_close_streak = state.adverse_close_streak + 1 if adverse else 0
        if state.adverse_close_streak >= self.config.acceptance_min_closes:
            _terminal(self, state, bar, "QUARTER_HOUR_RELOAD_CONTEXT_NOT_ACCEPTED")
            return None
        _confirm_extension(self, state, bar, atr)
        return None

    if state.state == "WAIT_RAID":
        if state.direction == Direction.LONG:
            context_failed = (
                bar.close
                <= state.opening_low - self.config.acceptance_retest_atr * atr
            )
            state.extension_extreme = max(float(state.extension_extreme), bar.high)
        else:
            context_failed = (
                bar.close
                >= state.opening_high + self.config.acceptance_retest_atr * atr
            )
            state.extension_extreme = min(float(state.extension_extreme), bar.low)
        if context_failed:
            _terminal(
                self,
                state,
                bar,
                "QUARTER_HOUR_RELOAD_DELIVERY_LEG_INVALIDATED_BEFORE_RAID",
            )
            return None
        _detect_raid(self, state, bar, prev, atr)
        return None

    if state.state == "WAIT_RECLAIM":
        _step_reclaim(self, state, bar, atr)
        return None

    if state.state == "WAIT_REACCELERATION":
        return _step_reacceleration(self, state, bar, atr)

    return None


BASE_ON_BAR: Callable[..., TradePlan | None] | None = None
BASE_MARK_SUBMITTED: Callable[..., None] | None = None
BASE_MARK_REJECTED: Callable[..., None] | None = None
BASE_MARK_TRADE_TERMINAL: Callable[..., None] | None = None


def candidate28_on_bar(
    self: CausalAuctionEngine,
    bar: BarObs,
    *,
    allow_entry: bool = True,
) -> TradePlan | None:
    if BASE_ON_BAR is None:
        raise RuntimeError("Candidate 28 is not installed")
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

    state: QuarterHourReloadState | None = getattr(
        self,
        "_candidate28_reload_state",
        None,
    )
    if state is None:
        _detect_context(self, bar, atr)
        return None

    plan = _step(self, state, bar, self.bars[-2], atr)
    if plan is not None and not allow_entry:
        candidate28_mark_rejected(
            self,
            plan,
            bar.ts_ns,
            "OUTSIDE_EVALUATION_WINDOW",
        )
        return None
    return plan


def candidate28_mark_submitted(
    self: CausalAuctionEngine,
    plan: TradePlan,
    quantity: Any,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_SUBMITTED is None:
        raise RuntimeError("Candidate 28 is not installed")
    state: QuarterHourReloadState | None = getattr(
        self,
        "_candidate28_reload_state",
        None,
    )
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        if (
            state is None
            or state.scenario_id != plan.scenario_id
            or state.state != "PLAN_CONFIRMED"
        ):
            raise RuntimeError("submitted quarter-hour reload plan does not match state")
        if self.active_trade_id is not None:
            raise RuntimeError("global candidate slot already occupied")
        if self.active is not None and self.bars:
            self._terminal(
                self.active,
                self.bars[-1],
                "QUARTER_HOUR_RELOAD_ALLOCATED_BEFORE_EXTERNAL_PLAN",
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
        self._candidate28_trade_kind = SCENARIO_KIND
        self._candidate28_reload_state = None
        self._candidate16_trade_kind = "OTHER"
        self._candidate16_submitted_far = None
        return

    if state is not None and self.bars:
        _terminal(self, state, self.bars[-1], "COMPETING_EXTERNAL_PLAN_ALLOCATED")
    BASE_MARK_SUBMITTED(self, plan, quantity, details)


def candidate28_mark_rejected(
    self: CausalAuctionEngine,
    plan: TradePlan,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_REJECTED is None:
        raise RuntimeError("Candidate 28 is not installed")
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        state: QuarterHourReloadState | None = getattr(
            self,
            "_candidate28_reload_state",
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
        self._candidate28_reload_state = None
        return
    BASE_MARK_REJECTED(self, plan, ts_ns, reason, details)


def candidate28_mark_trade_terminal(
    self: CausalAuctionEngine,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_TRADE_TERMINAL is None:
        raise RuntimeError("Candidate 28 is not installed")
    BASE_MARK_TRADE_TERMINAL(self, ts_ns, reason, details)
    self._candidate28_trade_kind = None


def install() -> None:
    global BASE_ON_BAR, BASE_MARK_SUBMITTED, BASE_MARK_REJECTED, BASE_MARK_TRADE_TERMINAL
    if CausalAuctionEngine.on_bar is candidate28_on_bar:
        return
    BASE_ON_BAR = CausalAuctionEngine.on_bar
    BASE_MARK_SUBMITTED = CausalAuctionEngine.mark_submitted
    BASE_MARK_REJECTED = CausalAuctionEngine.mark_rejected
    BASE_MARK_TRADE_TERMINAL = CausalAuctionEngine.mark_trade_terminal
    CausalAuctionEngine.on_bar = candidate28_on_bar
    CausalAuctionEngine.mark_submitted = candidate28_mark_submitted
    CausalAuctionEngine.mark_rejected = candidate28_mark_rejected
    CausalAuctionEngine.mark_trade_terminal = candidate28_mark_trade_terminal
