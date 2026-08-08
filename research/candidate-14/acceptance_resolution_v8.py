"""Candidate 14 v8 explicit accepted-auction failure resolution.

The v6 correction established that ordinary failed-auction reversal belongs only
to an exclusively rejection-framed liquidity event.  V7 then showed that both
the inherited second-pullback limit and immediate reacceleration entry failed to
turn the current AAC label into positive expectancy.  V8 therefore stops
trading the incomplete continuation label and represents a different causal
state:

    accepted-auction origin
    -> completed deep boundary re-entry (failure observation)
    -> no same-bar reversal
    -> later completed opposite initiative through the failure-bar extreme
    -> failure-bar invalidation and still-live opposing external draw

All magnitude conditions are inherited from the frozen detector.  Exclusive
rejection-origin FAR remains delegated byte-for-byte to the v6 adapter.  The
current AAC continuation branch is deliberately flat; a future candidate must
supply an independently observable durable-acceptance state before continuation
can trade again.

This module contains no order simulation, PnL ledger or NAV calculation.
NautilusTrader remains the sole execution and account engine.
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
_STATE_ATTR = "_candidate14_v8_acceptance_failure_states"


@dataclass(frozen=True, slots=True)
class AcceptanceFailureState:
    scenario_id: str
    failure_index: int
    failure_ts_ns: int
    failure_high: float
    failure_low: float
    boundary: float
    target_pool_id: str
    target_level: float


def _states(engine: CausalAuctionEngine) -> dict[str, AcceptanceFailureState]:
    value = getattr(engine, _STATE_ATTR, None)
    if value is None:
        value = {}
        setattr(engine, _STATE_ATTR, value)
    return value


def _opposite_target(
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
) -> AcceptanceFailureState | None:
    target = _opposite_target(self, a, bar.close)
    if target is None or target.consumed:
        self._terminal(a, bar, "AAC_FAILURE_WITHOUT_LIVE_OPPOSING_DRAW")
        return None
    opposite = Side.LOW if a.pool.side == Side.HIGH else Side.HIGH
    if target.side != opposite:
        self._terminal(a, bar, "AAC_FAILURE_TARGET_WRONG_SIDE")
        return None
    boundary = a.last_crossed_level if a.last_crossed_level is not None else a.pool.level
    state = AcceptanceFailureState(
        scenario_id=a.pool.scenario_id,
        failure_index=self._index,
        failure_ts_ns=bar.ts_ns,
        failure_high=bar.high,
        failure_low=bar.low,
        boundary=float(boundary),
        target_pool_id=target.scenario_id,
        target_level=float(target.level),
    )
    _states(self)[a.pool.scenario_id] = state
    self._event(
        a.pool.scenario_id,
        "AAC_FAILURE_OBSERVED",
        a.sweep.ts_ns,
        bar.ts_ns,
        "OBSERVE",
        "AAC_FAILURE_PENDING_INITIATIVE",
        "DEEP_BOUNDARY_REENTRY_REQUIRES_LATER_INITIATIVE",
        boundary,
        {
            "failure_high": bar.high,
            "failure_low": bar.low,
            "target_pool": target.scenario_id,
            "target": target.level,
            "same_bar_reversal_allowed": False,
        },
    )
    return state


def explicit_acceptance_failure_far(
    self: CausalAuctionEngine,
    a: Auction,
    bar: BarObs,
    preserved_far: ConfirmFar,
) -> TradePlan | None:
    """Delegate pure rejection FAR; stage every acceptance-origin reversal."""
    if far_origin_is_exclusive_rejection(a):
        return preserved_far(self, a, bar)
    if not a.acceptance_seed:
        return None

    states = _states(self)
    state = states.get(a.pool.scenario_id)

    # Use the already-frozen AAC invalidation observation before any reversal
    # decision.  This prevents the generic FAR branch from claiming the same
    # re-entry bar which first revealed acceptance failure.
    self._track_aac_pullback(a, bar)

    if state is not None and not a.acceptance_invalidated:
        states.pop(a.pool.scenario_id, None)
        self._event(
            a.pool.scenario_id,
            "AAC_FAILURE_RESCINDED",
            a.sweep.ts_ns,
            bar.ts_ns,
            "AAC_FAILURE_PENDING_INITIATIVE",
            "OBSERVE",
            "ACCEPTANCE_EXTREME_RESTORED",
            a.pool.level,
        )
        return None

    if a.acceptance_invalidated and state is None:
        _register_failure(self, a, bar)
        return None
    if state is None or self._index <= state.failure_index:
        return None

    target_pool = next(
        (
            pool
            for pool in self.pools
            if pool.scenario_id == state.target_pool_id
            and not pool.consumed
            and self._index <= pool.expiry_index
        ),
        None,
    )
    if target_pool is None:
        states.pop(a.pool.scenario_id, None)
        self._terminal(a, bar, "AAC_FAILURE_TARGET_NO_LONGER_LIVE")
        return None

    if a.pool.side == Side.HIGH:
        direction = Direction.SHORT
        displaced = bar.close < state.failure_low
        flow = bar.signed_flow <= -self.config.displacement_flow_min
        location = bar.close_location <= 0.40
        stop = state.failure_high + self.config.stop_buffer_atr * a.atr
    else:
        direction = Direction.LONG
        displaced = bar.close > state.failure_high
        flow = bar.signed_flow >= self.config.displacement_flow_min
        location = bar.close_location >= 0.60
        stop = state.failure_low - self.config.stop_buffer_atr * a.atr
    body = bar.body >= self.config.displacement_body_atr * a.atr
    if not (displaced and flow and location and body):
        return None

    entry = float(bar.close)
    target = float(state.target_level)
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
        states.pop(a.pool.scenario_id, None)
        self._terminal(a, bar, "AAC_FAILURE_INITIATIVE_NOT_COST_EXECUTABLE")
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
    states.pop(a.pool.scenario_id, None)

    self._event(
        a.pool.scenario_id,
        "AAC_FAILURE_REVERSAL_CONFIRMED",
        state.failure_ts_ns,
        bar.ts_ns,
        "AAC_FAILURE_PENDING_INITIATIVE",
        "FAR_CONFIRMED",
        "LATER_FAILURE_EXTREME_BREAK_WITH_ALIGNED_INITIATIVE",
        state.boundary,
        {
            "direction": direction.value,
            "failure_ts_ns": state.failure_ts_ns,
            "failure_high": state.failure_high,
            "failure_low": state.failure_low,
            "target_pool": target_pool.scenario_id,
            "target": target,
            "stop": stop,
            "same_bar_reversal_allowed": False,
        },
    )

    reason_code = "AAC_FAILURE_LATER_INITIATIVE_MARKET"
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
            "acceptance_failure_ts_ns": state.failure_ts_ns,
            "acceptance_failure_high": state.failure_high,
            "acceptance_failure_low": state.failure_low,
            "source_boundary": state.boundary,
            "target_pool": target_pool.scenario_id,
            "entry_model": "AAC_FAILURE_LATER_INITIATIVE_MARKET",
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
        state.failure_ts_ns,
        bar.ts_ns,
        "FAR_CONFIRMED",
        "PENDING_ENTRY",
        reason_code,
        entry,
        {
            "scenario": Scenario.FAR.value,
            "transition": "FAILED_ACCEPTANCE_REVERSAL",
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


def suppress_incomplete_acceptance_continuation(
    self: CausalAuctionEngine,
    a: Auction,
    bar: BarObs,
    preserved_aac: ConfirmAac,
) -> TradePlan | None:
    """Keep the current incomplete AAC label flat; preserve non-AAC behavior."""
    if not a.acceptance_seed:
        return preserved_aac(self, a, bar)
    return None


def install() -> None:
    preserved_far: ConfirmFar = CausalAuctionEngine._confirm_far
    preserved_aac: ConfirmAac = CausalAuctionEngine._confirm_aac

    def far_dispatch(
        self: CausalAuctionEngine,
        a: Auction,
        bar: BarObs,
    ) -> TradePlan | None:
        return explicit_acceptance_failure_far(self, a, bar, preserved_far)

    def aac_dispatch(
        self: CausalAuctionEngine,
        a: Auction,
        bar: BarObs,
    ) -> TradePlan | None:
        return suppress_incomplete_acceptance_continuation(
            self,
            a,
            bar,
            preserved_aac,
        )

    far_dispatch.__name__ = "candidate14_v8_confirm_far"
    aac_dispatch.__name__ = "candidate14_v8_confirm_aac"
    CausalAuctionEngine._confirm_far = far_dispatch
    CausalAuctionEngine._confirm_aac = aac_dispatch
