"""External-draw internal-liquidity raid continuation for Candidate 26.

This is a new SMC/ICT scenario, not another label on the external-range sweep
engine. It explicitly separates context liquidity from entry liquidity:

1. the already-causal external map must resolve a dominant live draw;
2. a completed, previously known five-minute internal pivot on the opposite
   side is raided during an existing regional decision window;
3. aggressive flow must participate in the raid;
4. price must reclaim that internal level;
5. a later completed minute must displace through the raid episode in the
   direction of the frozen external draw; and
6. the plan must still pass the portfolio runner's independent AAC
   cross-market semantics before any Nautilus order is submitted.

The target is the pre-existing external draw. The stop is beyond the actual
internal raid extreme. Entry and stop are costed as taker fills, the target as
a maker fill, and the unchanged minimum after-cost R and exact 3% current-NAV
loss budget apply. The module observes alongside the external detector but
emits at most one plan; an already-complete external plan has priority on the
same bar.
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

SCENARIO_KIND = "EXTERNAL_DRAW_INTERNAL_LIQUIDITY_RAID"


@dataclass(slots=True)
class InternalRaidState:
    scenario_id: str
    direction: Direction
    trigger_side: Side
    trigger_level: float
    trigger_candidate_ts_ns: int
    trigger_known_ts_ns: int
    target_pool_id: str
    target_price: float
    target_source: str
    target_strength: int
    draw_score: float
    decision_end_ts_ns: int
    sweep_ts_ns: int
    sweep_index: int
    expiry_index: int
    sweep_extreme: float
    episode_break_level: float
    state: str = "WAIT_RECLAIM"
    adverse_outside_streak: int = 0
    reclaim_ts_ns: int | None = None
    reclaim_index: int | None = None


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


def _decision_window_end(self: CausalAuctionEngine, ts_ns: int) -> int | None:
    ends = [
        int(pool.trigger_end_ts_ns)
        for pool in self.pools
        if pool.triggerable
        and pool.trigger_start_ts_ns <= ts_ns <= pool.trigger_end_ts_ns
    ]
    return max(ends) if ends else None


def _target_is_live(self: CausalAuctionEngine, state: InternalRaidState) -> bool:
    return any(
        pool.scenario_id == state.target_pool_id
        and not pool.consumed
        and self._index <= pool.expiry_index
        for pool in self.pools
    )


def _terminal(
    self: CausalAuctionEngine,
    state: InternalRaidState,
    bar: BarObs,
    reason: str,
) -> None:
    self._event(
        state.scenario_id,
        "INTERNAL_RAID_TERMINAL",
        state.sweep_ts_ns,
        bar.ts_ns,
        state.state,
        "TERMINAL",
        reason,
        state.trigger_level,
        {
            "direction": state.direction.value,
            "trigger_side": state.trigger_side.value,
            "trigger_level": state.trigger_level,
            "target_pool": state.target_pool_id,
            "target": state.target_price,
            "target_source": state.target_source,
            "sweep_extreme": state.sweep_extreme,
        },
    )
    self.skips[reason] += 1
    self._candidate26_internal_raid_state = None


def _consumed_keys(self: CausalAuctionEngine) -> set[tuple[str, int, int, float]]:
    keys = getattr(self, "_candidate26_consumed_internal_keys", None)
    if keys is None:
        keys = set()
        self._candidate26_consumed_internal_keys = keys
    return keys


def _select_crossed_pivot(
    self: CausalAuctionEngine,
    bar: BarObs,
    prev: BarObs,
    atr: float,
    direction: Direction,
) -> tuple[int, int, float, float] | None:
    side = Side.LOW if direction == Direction.LONG else Side.HIGH
    pivots = self.internal_lows if side == Side.LOW else self.internal_highs
    cutoff = bar.ts_ns - self.config.event_expiry_bars * MINUTE_NS
    consumed = _consumed_keys(self)
    eligible: list[tuple[int, int, float, float]] = []
    for candidate_ts_ns, known_ts_ns, level in pivots:
        key = (side.value, int(candidate_ts_ns), int(known_ts_ns), round(float(level), 10))
        if key in consumed or known_ts_ns >= bar.ts_ns or known_ts_ns < cutoff:
            continue
        if direction == Direction.LONG:
            crossed = prev.close >= level > bar.low
            penetration = (level - bar.low) / atr
        else:
            crossed = prev.close <= level < bar.high
            penetration = (bar.high - level) / atr
        if crossed and self.config.sweep_min_atr <= penetration <= self.config.sweep_max_atr:
            eligible.append((int(candidate_ts_ns), int(known_ts_ns), float(level), penetration))
    if not eligible:
        return None
    # The nearest crossed internal pool is the first economically reachable
    # resting liquidity. A deeper cascade belongs to a later episode.
    return (
        max(eligible, key=lambda item: item[2])
        if direction == Direction.LONG
        else min(eligible, key=lambda item: item[2])
    )


def _detect(
    self: CausalAuctionEngine,
    bar: BarObs,
    prev: BarObs,
    atr: float,
) -> None:
    if getattr(self, "_candidate26_internal_raid_state", None) is not None:
        return
    if self.active_trade_id is not None:
        return
    decision_end = _decision_window_end(self, bar.ts_ns)
    if decision_end is None:
        return
    median_volume = self.median_volume
    if median_volume is None or median_volume <= 0.0:
        return
    relative_volume = bar.volume / median_volume
    if relative_volume < self.config.min_relative_volume:
        return

    draw_side, draw_score, high_pool, low_pool, high_hazard, low_hazard = self._draw_resolution(
        prev.close,
        atr,
    )
    if draw_side is None:
        return
    direction = Direction.LONG if draw_side == Side.HIGH else Direction.SHORT
    target_pool = high_pool if draw_side == Side.HIGH else low_pool
    if (
        target_pool is None
        or target_pool.consumed
        or not target_pool.external
        or target_pool.confirmed_index >= self._index
        or self._index > target_pool.expiry_index
    ):
        return
    target_ahead = (
        target_pool.level > bar.close
        if direction == Direction.LONG
        else target_pool.level < bar.close
    )
    if not target_ahead:
        return

    pivot = _select_crossed_pivot(self, bar, prev, atr, direction)
    if pivot is None:
        return
    candidate_ts_ns, known_ts_ns, level, penetration = pivot
    flow_ok = (
        bar.signed_flow <= -self.config.absorption_flow_min
        if direction == Direction.LONG
        else bar.signed_flow >= self.config.absorption_flow_min
    )
    if not flow_ok:
        self.skips["INTERNAL_RAID_WITHOUT_AGGRESSOR_FLOW"] += 1
        return

    trigger_side = Side.LOW if direction == Direction.LONG else Side.HIGH
    key = (trigger_side.value, candidate_ts_ns, known_ts_ns, round(level, 10))
    _consumed_keys(self).add(key)
    scenario_id = f"{self.instrument_id}-ILR-{known_ts_ns}-{bar.ts_ns}-{direction.value}"
    state = InternalRaidState(
        scenario_id=scenario_id,
        direction=direction,
        trigger_side=trigger_side,
        trigger_level=level,
        trigger_candidate_ts_ns=candidate_ts_ns,
        trigger_known_ts_ns=known_ts_ns,
        target_pool_id=target_pool.scenario_id,
        target_price=float(target_pool.level),
        target_source=str(target_pool.source),
        target_strength=int(target_pool.strength),
        draw_score=float(draw_score),
        decision_end_ts_ns=decision_end,
        sweep_ts_ns=bar.ts_ns,
        sweep_index=self._index,
        expiry_index=self._index + self.config.event_expiry_bars,
        sweep_extreme=bar.low if direction == Direction.LONG else bar.high,
        episode_break_level=bar.high if direction == Direction.LONG else bar.low,
    )
    self._candidate26_internal_raid_state = state
    self._event(
        scenario_id,
        "INTERNAL_LIQUIDITY_RAID_DETECTED",
        candidate_ts_ns,
        bar.ts_ns,
        "INTERNAL_POOL_ARMED",
        "WAIT_RECLAIM",
        "OPPOSITE_INTERNAL_LIQUIDITY_TRADED_THROUGH",
        level,
        {
            "direction": direction.value,
            "trigger_side": trigger_side.value,
            "trigger_known_ts_ns": known_ts_ns,
            "penetration_atr": penetration,
            "relative_volume": relative_volume,
            "aggregate_aggressor_flow": bar.signed_flow,
            "draw_side": draw_side.value,
            "draw_score": draw_score,
            "high_hazard": high_hazard,
            "low_hazard": low_hazard,
            "target_pool": target_pool.scenario_id,
            "target": target_pool.level,
            "target_source": target_pool.source,
            "decision_end_ts_ns": decision_end,
        },
    )


def _plan(
    self: CausalAuctionEngine,
    state: InternalRaidState,
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
        _terminal(self, state, bar, "INTERNAL_RAID_INSUFFICIENT_COSTED_R")
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
        reason_code="EXTERNAL_DRAW_INTERNAL_RAID_RECLAIM_DISPLACEMENT_MARKET",
        expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,
        entry_order_type="MARKET",
        entry_post_only=False,
        details={
            "scenario_kind": SCENARIO_KIND,
            "sweep_ts_ns": state.sweep_ts_ns,
            "internal_trigger_side": state.trigger_side.value,
            "internal_trigger_level": state.trigger_level,
            "internal_trigger_candidate_ts_ns": state.trigger_candidate_ts_ns,
            "internal_trigger_known_ts_ns": state.trigger_known_ts_ns,
            "sweep_extreme": state.sweep_extreme,
            "reclaim_ts_ns": state.reclaim_ts_ns,
            "episode_break_level": state.episode_break_level,
            "draw_score": state.draw_score,
            "target_pool": state.target_pool_id,
            "target_source": state.target_source,
            "target_strength": state.target_strength,
            "entry_model": "COMPLETED_RECLAIM_THEN_EPISODE_DISPLACEMENT_MARKET",
            "stop_model": "INTERNAL_RAID_EXTREME_INVALIDATION",
            "entry_cost_assumption": "TAKER",
            "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,
        },
    )
    self._event(
        state.scenario_id,
        "TRADE_PLAN_CONFIRMED",
        state.sweep_ts_ns,
        bar.ts_ns,
        "WAIT_DISPLACEMENT",
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
    state: InternalRaidState,
    bar: BarObs,
    atr: float,
) -> TradePlan | None:
    if self._index > state.expiry_index:
        _terminal(self, state, bar, "INTERNAL_RAID_EXPIRED")
        return None
    if bar.ts_ns > state.decision_end_ts_ns:
        _terminal(self, state, bar, "INTERNAL_RAID_DECISION_WINDOW_EXPIRED")
        return None
    if not _target_is_live(self, state):
        _terminal(self, state, bar, "INTERNAL_RAID_EXTERNAL_TARGET_NO_LONGER_LIVE")
        return None
    target_reached = (
        bar.high >= state.target_price
        if state.direction == Direction.LONG
        else bar.low <= state.target_price
    )
    if target_reached:
        _terminal(self, state, bar, "INTERNAL_RAID_TARGET_REACHED_BEFORE_ENTRY")
        return None

    if state.state == "WAIT_RECLAIM":
        if state.direction == Direction.LONG:
            state.sweep_extreme = min(state.sweep_extreme, bar.low)
            state.episode_break_level = max(state.episode_break_level, bar.high)
            adverse = bar.close <= state.trigger_level - self.config.acceptance_close_atr * atr
            reclaimed = bar.close >= state.trigger_level + self.config.rejection_reclaim_atr * atr
        else:
            state.sweep_extreme = max(state.sweep_extreme, bar.high)
            state.episode_break_level = min(state.episode_break_level, bar.low)
            adverse = bar.close >= state.trigger_level + self.config.acceptance_close_atr * atr
            reclaimed = bar.close <= state.trigger_level - self.config.rejection_reclaim_atr * atr
        state.adverse_outside_streak = state.adverse_outside_streak + 1 if adverse else 0
        if state.adverse_outside_streak >= self.config.acceptance_min_closes:
            _terminal(self, state, bar, "INTERNAL_RAID_ACCEPTED_AGAINST_EXTERNAL_DRAW")
            return None
        if reclaimed:
            state.state = "WAIT_DISPLACEMENT"
            state.reclaim_ts_ns = bar.ts_ns
            state.reclaim_index = self._index
            self._event(
                state.scenario_id,
                "INTERNAL_RAID_RECLAIM_CONFIRMED",
                state.sweep_ts_ns,
                bar.ts_ns,
                "WAIT_RECLAIM",
                "WAIT_DISPLACEMENT",
                "INTERNAL_POOL_RECLAIMED_AFTER_RAID",
                state.trigger_level,
                {
                    "direction": state.direction.value,
                    "sweep_extreme": state.sweep_extreme,
                    "episode_break_level": state.episode_break_level,
                },
            )
        return None

    if state.state != "WAIT_DISPLACEMENT":
        return None

    # A fresh extension is a new raid episode, not a free stop widening after a
    # plan exists. It must reclaim again and then displace on a later bar.
    extended = (
        bar.low < state.sweep_extreme
        if state.direction == Direction.LONG
        else bar.high > state.sweep_extreme
    )
    if extended:
        state.sweep_extreme = bar.low if state.direction == Direction.LONG else bar.high
        state.episode_break_level = (
            max(state.episode_break_level, bar.high)
            if state.direction == Direction.LONG
            else min(state.episode_break_level, bar.low)
        )
        state.state = "WAIT_RECLAIM"
        state.reclaim_ts_ns = None
        state.reclaim_index = None
        state.adverse_outside_streak = 0
        self._event(
            state.scenario_id,
            "INTERNAL_RAID_EPISODE_EXTENDED",
            state.sweep_ts_ns,
            bar.ts_ns,
            "WAIT_DISPLACEMENT",
            "WAIT_RECLAIM",
            "DEEPER_INTERNAL_LIQUIDITY_RAID",
            state.sweep_extreme,
            {"episode_break_level": state.episode_break_level},
        )
        return None
    if state.reclaim_index is None or self._index <= state.reclaim_index:
        return None

    if state.direction == Direction.LONG:
        broke = bar.close > state.episode_break_level
        flow = bar.signed_flow >= self.config.displacement_flow_min
        location = bar.close_location >= self.config.acceptance_close_location
    else:
        broke = bar.close < state.episode_break_level
        flow = bar.signed_flow <= -self.config.displacement_flow_min
        location = bar.close_location <= 1.0 - self.config.acceptance_close_location
    body = bar.body >= self.config.displacement_body_atr * atr
    if not (broke and flow and location and body):
        return None
    return _plan(self, state, bar, atr)


BASE_ON_BAR: Callable[..., TradePlan | None] | None = None
BASE_MARK_SUBMITTED: Callable[..., None] | None = None
BASE_MARK_REJECTED: Callable[..., None] | None = None
BASE_MARK_TRADE_TERMINAL: Callable[..., None] | None = None


def candidate26_on_bar(
    self: CausalAuctionEngine,
    bar: BarObs,
    *,
    allow_entry: bool = True,
) -> TradePlan | None:
    if BASE_ON_BAR is None:
        raise RuntimeError("Candidate 26 is not installed")
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
    state: InternalRaidState | None = getattr(self, "_candidate26_internal_raid_state", None)
    if state is None:
        _detect(self, bar, self.bars[-2], atr)
        state = getattr(self, "_candidate26_internal_raid_state", None)
        if state is None:
            return None
    plan = _step(self, state, bar, atr)
    if plan is not None and not allow_entry:
        candidate26_mark_rejected(self, plan, bar.ts_ns, "OUTSIDE_EVALUATION_WINDOW")
        return None
    return plan


def candidate26_mark_submitted(
    self: CausalAuctionEngine,
    plan: TradePlan,
    quantity: Any,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_SUBMITTED is None:
        raise RuntimeError("Candidate 26 is not installed")
    state: InternalRaidState | None = getattr(self, "_candidate26_internal_raid_state", None)
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        if state is None or state.scenario_id != plan.scenario_id or state.state != "PLAN_CONFIRMED":
            raise RuntimeError("submitted internal-raid plan does not match pending state")
        if self.active_trade_id is not None:
            raise RuntimeError("global candidate slot already occupied")
        if self.active is not None and self.bars:
            self._terminal(self.active, self.bars[-1], "INTERNAL_RAID_ALLOCATED_BEFORE_EXTERNAL_PLAN")
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
        self._candidate26_trade_kind = SCENARIO_KIND
        self._candidate26_internal_raid_state = None
        self._candidate16_trade_kind = "OTHER"
        self._candidate16_submitted_far = None
        return

    if state is not None and self.bars:
        _terminal(self, state, self.bars[-1], "COMPETING_EXTERNAL_PLAN_ALLOCATED")
    BASE_MARK_SUBMITTED(self, plan, quantity, details)


def candidate26_mark_rejected(
    self: CausalAuctionEngine,
    plan: TradePlan,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_REJECTED is None:
        raise RuntimeError("Candidate 26 is not installed")
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        state: InternalRaidState | None = getattr(self, "_candidate26_internal_raid_state", None)
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
        self._candidate26_internal_raid_state = None
        return
    BASE_MARK_REJECTED(self, plan, ts_ns, reason, details)


def candidate26_mark_trade_terminal(
    self: CausalAuctionEngine,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_TRADE_TERMINAL is None:
        raise RuntimeError("Candidate 26 is not installed")
    BASE_MARK_TRADE_TERMINAL(self, ts_ns, reason, details)
    self._candidate26_trade_kind = None


def install() -> None:
    global BASE_ON_BAR, BASE_MARK_SUBMITTED, BASE_MARK_REJECTED, BASE_MARK_TRADE_TERMINAL
    if CausalAuctionEngine.on_bar is candidate26_on_bar:
        return
    # Capture after Candidate 16/C25 has installed its lifecycle hooks. Import
    # order therefore cannot bypass the post-stop state or native exit reasons.
    BASE_ON_BAR = CausalAuctionEngine.on_bar
    BASE_MARK_SUBMITTED = CausalAuctionEngine.mark_submitted
    BASE_MARK_REJECTED = CausalAuctionEngine.mark_rejected
    BASE_MARK_TRADE_TERMINAL = CausalAuctionEngine.mark_trade_terminal
    CausalAuctionEngine.on_bar = candidate26_on_bar
    CausalAuctionEngine.mark_submitted = candidate26_mark_submitted
    CausalAuctionEngine.mark_rejected = candidate26_mark_rejected
    CausalAuctionEngine.mark_trade_terminal = candidate26_mark_trade_terminal
