"""Candidate 14 v9 confirmed accepted-auction failure resolution.

V8 demonstrated why an acceptance *possibility* cannot be treated as a completed
accepted auction: generic deep re-entry and a later opposite bar produced 128
reversal instructions before the corresponding scenarios would have satisfied
the frozen AAC completion sequence.

V9 therefore separates three causal substates while the parent auction remains
in OBSERVE:

    frozen AAC hold + defended pullback + later reacceleration
    -> ACCEPTANCE_COMPLETION_OBSERVED (no trade)
    -> later deep boundary re-entry
    -> ACCEPTANCE_FAILURE_OBSERVED (no same-bar reversal)
    -> later opposite initiative through the failure-bar extreme
    -> failure-bar invalidation and still-live opposing external draw

The AAC completion observer mirrors the preserved detector's categorical and
magnitude conditions but deliberately emits no continuation order.  Ordinary
FAR remains byte-delegated only for an exclusively rejection-origin auction.
No order simulation, PnL ledger or NAV calculation exists here; NautilusTrader
remains the sole execution and account engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from auction_origin_ownership import far_origin_is_exclusive_rejection
from logic import (
    Auction,
    BarObs,
    CausalAuctionEngine,
    Direction,
    Scenario,
    Side,
    TradePlan,
)
from semantic_execution import MARKET_ENTRY_SENTINEL_NS
from semantic_logic import qualify_market_entry


ConfirmFar = Callable[[CausalAuctionEngine, Auction, BarObs], TradePlan | None]
ConfirmAac = Callable[[CausalAuctionEngine, Auction, BarObs], TradePlan | None]
_COMPLETIONS_ATTR = "_candidate14_v9_acceptance_completions"
_FAILURES_ATTR = "_candidate14_v9_acceptance_failures"


@dataclass(frozen=True, slots=True)
class AcceptanceCompletionState:
    scenario_id: str
    completion_index: int
    completion_ts_ns: int
    direction: Direction
    boundary: float
    pullback_extreme: float
    accepted_extreme: float
    continuation_target_pool_id: str
    continuation_target_level: float


@dataclass(frozen=True, slots=True)
class AcceptanceFailureState:
    scenario_id: str
    completion_ts_ns: int
    failure_index: int
    failure_ts_ns: int
    failure_high: float
    failure_low: float
    boundary: float
    target_pool_id: str
    target_level: float


def _state_map(
    engine: CausalAuctionEngine,
    attribute: str,
) -> dict[str, object]:
    value = getattr(engine, attribute, None)
    if value is None:
        value = {}
        setattr(engine, attribute, value)
    return value


def _completions(
    engine: CausalAuctionEngine,
) -> dict[str, AcceptanceCompletionState]:
    return _state_map(engine, _COMPLETIONS_ATTR)  # type: ignore[return-value]


def _failures(
    engine: CausalAuctionEngine,
) -> dict[str, AcceptanceFailureState]:
    return _state_map(engine, _FAILURES_ATTR)  # type: ignore[return-value]


def observe_completed_acceptance(
    self: CausalAuctionEngine,
    a: Auction,
    bar: BarObs,
) -> AcceptanceCompletionState | None:
    """Record the frozen AAC completion sequence without creating an order."""
    if not a.acceptance_seed:
        return None
    existing = _completions(self).get(a.pool.scenario_id)
    if existing is not None:
        return existing
    if a.cascade_count < 2 and (
        a.framed_draw_side is None or a.framed_draw_side != a.pool.side
    ):
        return None

    # Before completion this is the only v9 path which advances AAC pullback
    # state, so outside streaks are counted exactly once per completed bar.
    self._track_aac_pullback(a, bar)
    if bar.ts_ns < a.pool.trigger_start_ts_ns:
        return None
    if bar.ts_ns > a.pool.trigger_end_ts_ns:
        self._terminal(a, bar, "SESSION_DECISION_WINDOW_EXPIRED")
        return None
    if a.acceptance_invalidated:
        return None
    if (
        a.pullback_known_index is None
        or a.acceptance_impulse_extreme is None
        or a.pullback_extreme is None
        or self._index <= a.pullback_known_index
    ):
        return None

    side = a.pool.side
    boundary = float(
        a.last_crossed_level
        if a.last_crossed_level is not None
        else a.pool.level
    )
    if side == Side.HIGH:
        reaccelerated = bar.close > a.acceptance_impulse_extreme
        flow = bar.signed_flow >= self.config.reacceleration_flow_min
        location = bar.close_location >= 0.60
        direction = Direction.LONG
        target_id = a.continuation_target_pool_id or a.framed_target_pool_id
        target_level = (
            a.continuation_target_level
            if a.continuation_target_level is not None
            else a.framed_target_level
        )
    else:
        reaccelerated = bar.close < a.acceptance_impulse_extreme
        flow = bar.signed_flow <= -self.config.reacceleration_flow_min
        location = bar.close_location <= 0.40
        direction = Direction.SHORT
        target_id = a.continuation_target_pool_id or a.framed_target_pool_id
        target_level = (
            a.continuation_target_level
            if a.continuation_target_level is not None
            else a.framed_target_level
        )
    body = bar.body >= self.config.reacceleration_body_atr * a.atr
    if not (reaccelerated and flow and location and body):
        return None

    target_pool = next(
        (
            pool
            for pool in self.pools
            if pool.scenario_id == target_id
            and not pool.consumed
            and self._index <= pool.expiry_index
        ),
        None,
    )
    if target_pool is None or target_level is None:
        self._terminal(a, bar, "CONTINUATION_TARGET_NO_LONGER_LIVE")
        return None
    if a.framed_draw_method != "EXTERNAL_HAZARD_DOMINANCE":
        self._terminal(a, bar, "AAC_REQUIRES_INDEPENDENT_EXTERNAL_DRAW")
        return None

    state = AcceptanceCompletionState(
        scenario_id=a.pool.scenario_id,
        completion_index=self._index,
        completion_ts_ns=bar.ts_ns,
        direction=direction,
        boundary=boundary,
        pullback_extreme=float(a.pullback_extreme),
        accepted_extreme=float(a.acceptance_impulse_extreme),
        continuation_target_pool_id=target_pool.scenario_id,
        continuation_target_level=float(target_level),
    )
    _completions(self)[a.pool.scenario_id] = state
    self._event(
        a.pool.scenario_id,
        "ACCEPTANCE_COMPLETION_OBSERVED",
        a.sweep.ts_ns,
        bar.ts_ns,
        "OBSERVE",
        "OBSERVE",
        "OUTSIDE_HOLD_CAUSAL_PULLBACK_REACCELERATION_UNTRADED",
        a.pool.level,
        {
            "episode_substate": "ACCEPTANCE_COMPLETED_UNTRADED",
            "direction": direction.value,
            "boundary": boundary,
            "pullback_extreme": state.pullback_extreme,
            "accepted_extreme": state.accepted_extreme,
            "continuation_target_pool": target_pool.scenario_id,
            "continuation_target": target_level,
            "continuation_order_allowed": False,
        },
    )
    return state


def _opposing_target(
    self: CausalAuctionEngine,
    a: Auction,
    price: float,
):
    target = next(
        (
            pool
            for pool in self.pools
            if pool.scenario_id == a.reversal_target_pool_id
            and not pool.consumed
            and self._index <= pool.expiry_index
        ),
        None,
    )
    if target is not None:
        return target
    return self._far_target_pool(a.pool, price)


def _register_failure(
    self: CausalAuctionEngine,
    a: Auction,
    bar: BarObs,
    completion: AcceptanceCompletionState,
) -> AcceptanceFailureState | None:
    target = _opposing_target(self, a, bar.close)
    if target is None or target.consumed or self._index > target.expiry_index:
        self._terminal(a, bar, "CONFIRMED_ACCEPTANCE_FAILURE_WITHOUT_LIVE_DRAW")
        return None
    opposite = Side.LOW if a.pool.side == Side.HIGH else Side.HIGH
    if target.side != opposite:
        self._terminal(a, bar, "CONFIRMED_ACCEPTANCE_FAILURE_TARGET_WRONG_SIDE")
        return None

    state = AcceptanceFailureState(
        scenario_id=a.pool.scenario_id,
        completion_ts_ns=completion.completion_ts_ns,
        failure_index=self._index,
        failure_ts_ns=bar.ts_ns,
        failure_high=bar.high,
        failure_low=bar.low,
        boundary=completion.boundary,
        target_pool_id=target.scenario_id,
        target_level=float(target.level),
    )
    _failures(self)[a.pool.scenario_id] = state
    self._event(
        a.pool.scenario_id,
        "CONFIRMED_ACCEPTANCE_FAILURE_OBSERVED",
        completion.completion_ts_ns,
        bar.ts_ns,
        "OBSERVE",
        "OBSERVE",
        "COMPLETED_ACCEPTANCE_DEEP_BOUNDARY_REENTRY",
        completion.boundary,
        {
            "episode_substate": "CONFIRMED_ACCEPTANCE_FAILURE_PENDING_INITIATIVE",
            "completion_ts_ns": completion.completion_ts_ns,
            "failure_high": bar.high,
            "failure_low": bar.low,
            "target_pool": target.scenario_id,
            "target": target.level,
            "same_bar_reversal_allowed": False,
        },
    )
    return state


def resolve_confirmed_acceptance_failure(
    self: CausalAuctionEngine,
    a: Auction,
    bar: BarObs,
    preserved_far: ConfirmFar,
) -> TradePlan | None:
    """Trade only a later initiative after a genuinely completed acceptance fails."""
    if far_origin_is_exclusive_rejection(a):
        return preserved_far(self, a, bar)
    if not a.acceptance_seed:
        return None

    completion = _completions(self).get(a.pool.scenario_id)
    if completion is None:
        # Pullback/acceptance state is advanced later by the AAC observer exactly
        # once on this bar.  No failure can exist before completion.
        return None

    # After completion, this FAR path is the sole owner of pullback/invalidation
    # updates; the AAC observer returns immediately for an existing completion.
    self._track_aac_pullback(a, bar)
    failures = _failures(self)
    failure = failures.get(a.pool.scenario_id)

    if failure is not None and not a.acceptance_invalidated:
        failures.pop(a.pool.scenario_id, None)
        self._event(
            a.pool.scenario_id,
            "CONFIRMED_ACCEPTANCE_FAILURE_RESCINDED",
            failure.failure_ts_ns,
            bar.ts_ns,
            "OBSERVE",
            "OBSERVE",
            "ACCEPTANCE_EXTREME_RESTORED",
            a.pool.level,
            {"episode_substate": "ACCEPTANCE_COMPLETED_UNTRADED"},
        )
        return None

    if a.acceptance_invalidated and failure is None:
        if self._index <= completion.completion_index:
            return None
        _register_failure(self, a, bar, completion)
        return None
    if failure is None or self._index <= failure.failure_index:
        return None

    target_pool = next(
        (
            pool
            for pool in self.pools
            if pool.scenario_id == failure.target_pool_id
            and not pool.consumed
            and self._index <= pool.expiry_index
        ),
        None,
    )
    if target_pool is None:
        failures.pop(a.pool.scenario_id, None)
        self._terminal(a, bar, "CONFIRMED_ACCEPTANCE_FAILURE_DRAW_NO_LONGER_LIVE")
        return None

    if a.pool.side == Side.HIGH:
        direction = Direction.SHORT
        displaced = bar.close < failure.failure_low
        flow = bar.signed_flow <= -self.config.displacement_flow_min
        location = bar.close_location <= 0.40
        stop = failure.failure_high + self.config.stop_buffer_atr * a.atr
    else:
        direction = Direction.LONG
        displaced = bar.close > failure.failure_high
        flow = bar.signed_flow >= self.config.displacement_flow_min
        location = bar.close_location >= 0.60
        stop = failure.failure_low - self.config.stop_buffer_atr * a.atr
    body = bar.body >= self.config.displacement_body_atr * a.atr
    if not (displaced and flow and location and body):
        return None

    entry = float(bar.close)
    target = float(failure.target_level)
    qualified, _risk, loss, net_gain, net_r = qualify_market_entry(
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        atr=a.atr,
        min_stop_atr=self.config.min_stop_atr,
        min_net_r=self.config.min_net_r,
        taker_rate=self.config.effective_taker_rate,
        target_maker_rate=self.config.effective_maker_rate,
    )
    if not qualified:
        failures.pop(a.pool.scenario_id, None)
        self._terminal(a, bar, "CONFIRMED_ACCEPTANCE_FAILURE_LEG_NOT_COST_EXECUTABLE")
        return None

    a.state = "FAR_CONFIRMED"
    a.scenario = Scenario.FAR
    a.direction = direction
    a.stop_price = stop
    a.target_price = target
    a.draw_side = target_pool.side
    a.draw_score = 1.0
    a.displacement_index = self._index
    a.zone_low, a.zone_high = self._zone_from_displacement(
        self.bars,
        self._index,
        direction,
    )
    a.elapsed = 0
    failures.pop(a.pool.scenario_id, None)

    self._event(
        a.pool.scenario_id,
        "CONFIRMED_ACCEPTANCE_FAILURE_REVERSAL",
        failure.failure_ts_ns,
        bar.ts_ns,
        "OBSERVE",
        "FAR_CONFIRMED",
        "LATER_FAILURE_EXTREME_BREAK_WITH_ALIGNED_INITIATIVE",
        failure.boundary,
        {
            "episode_substate": "CONFIRMED_ACCEPTANCE_FAILURE_RESOLVED",
            "completion_ts_ns": failure.completion_ts_ns,
            "failure_ts_ns": failure.failure_ts_ns,
            "direction": direction.value,
            "failure_high": failure.failure_high,
            "failure_low": failure.failure_low,
            "target_pool": target_pool.scenario_id,
            "target": target,
            "stop": stop,
            "same_bar_reversal_allowed": False,
        },
    )

    reason_code = "CONFIRMED_ACCEPTANCE_FAILURE_LATER_INITIATIVE_MARKET"
    plan = TradePlan(
        scenario_id=a.pool.scenario_id,
        scenario=Scenario.FAR,
        direction=direction,
        observed_ts_ns=bar.ts_ns,
        expected_entry=entry,
        stop_price=stop,
        target_price=target,
        atr=a.atr,
        loss_per_unit=loss,
        gain_per_unit=net_gain,
        net_r=net_r,
        reason_code=reason_code,
        expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,
        entry_order_type="MARKET",
        entry_post_only=False,
        details={
            "pool_level": a.pool.level,
            "pool_source": a.pool.source,
            "range_id": a.pool.range_id,
            "sweep_ts_ns": (
                a.initial_sweep_ts_ns
                if a.initial_sweep_ts_ns is not None
                else a.sweep.ts_ns
            ),
            "sweep_extreme": a.sweep_extreme,
            "acceptance_completion_ts_ns": failure.completion_ts_ns,
            "acceptance_failure_ts_ns": failure.failure_ts_ns,
            "acceptance_failure_high": failure.failure_high,
            "acceptance_failure_low": failure.failure_low,
            "source_boundary": failure.boundary,
            "target_pool": target_pool.scenario_id,
            "entry_model": "CONFIRMED_ACCEPTANCE_FAILURE_LATER_INITIATIVE_MARKET",
            "stop_model": "FAILURE_BAR_EXTREME_INVALIDATION",
            "entry_cost_assumption": "TAKER",
            "entry_expiry_bars": 0,
            "entry_expiry_structure_minutes": 0,
            "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,
            "same_bar_reversal_allowed": False,
        },
    )
    self._event(
        a.pool.scenario_id,
        "TRADE_PLAN_CONFIRMED",
        failure.failure_ts_ns,
        bar.ts_ns,
        "FAR_CONFIRMED",
        "PENDING_ENTRY",
        reason_code,
        entry,
        {
            "scenario": Scenario.FAR.value,
            "transition": "CONFIRMED_ACCEPTANCE_FAILURE_REVERSAL",
            "direction": direction.value,
            "entry_order_type": "MARKET",
            "entry_post_only": False,
            "target": target,
            "stop": stop,
            "net_r": net_r,
            "same_bar_reversal_allowed": False,
        },
    )
    a.state = "PENDING_ENTRY"
    return plan


def observe_acceptance_without_continuation(
    self: CausalAuctionEngine,
    a: Auction,
    bar: BarObs,
    preserved_aac: ConfirmAac,
) -> TradePlan | None:
    """Observe frozen AAC completion, but keep the incomplete continuation flat."""
    if not a.acceptance_seed:
        return preserved_aac(self, a, bar)
    observe_completed_acceptance(self, a, bar)
    return None


def install() -> None:
    preserved_far: ConfirmFar = CausalAuctionEngine._confirm_far
    preserved_aac: ConfirmAac = CausalAuctionEngine._confirm_aac

    def far_dispatch(
        self: CausalAuctionEngine,
        a: Auction,
        bar: BarObs,
    ) -> TradePlan | None:
        return resolve_confirmed_acceptance_failure(self, a, bar, preserved_far)

    def aac_dispatch(
        self: CausalAuctionEngine,
        a: Auction,
        bar: BarObs,
    ) -> TradePlan | None:
        return observe_acceptance_without_continuation(
            self,
            a,
            bar,
            preserved_aac,
        )

    far_dispatch.__name__ = "candidate14_v9_confirm_far"
    aac_dispatch.__name__ = "candidate14_v9_confirm_aac"
    CausalAuctionEngine._confirm_far = far_dispatch
    CausalAuctionEngine._confirm_aac = aac_dispatch
