#!/usr/bin/env python3
"""Causal contracts for the LCOR failed-ownership reversal router."""

from __future__ import annotations

from causal_inventory_ownership_transfer_engine import _OwnershipEpisode
from liquidation_cash_ownership_failure_router_engine import (
    LiquidationCashOwnershipFailureRouterEngine,
)
from lrb_types import BarObservation, PrimitiveSnapshot

MINUTE_NS = 60_000_000_000


def observation(
    index: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    *,
    flow: float,
) -> BarObservation:
    return BarObservation(
        ts_ns=(index + 1) * MINUTE_NS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=50.0 * (flow + 1.0),
        trades=10,
    )


def snapshot(
    index: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    *,
    flow: float,
) -> PrimitiveSnapshot:
    obs = observation(index, open_, high, low, close, flow=flow)
    return PrimitiveSnapshot(
        index=index,
        observation=obs,
        ready=True,
        atr=1.0,
        rel_volume=1.0,
        flow_ratio=flow,
        body_atr=abs(close - open_),
        range_atr=high - low,
        upper_wick_fraction=0.0,
        lower_wick_fraction=0.0,
        close_location=(close - low) / (high - low),
        upper_fast=104.0,
        lower_fast=96.0,
        upper_slow=105.0,
        lower_slow=95.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=0,
        lower_pool_touches=0,
    )


PARAMS = {
    "lcor_enable_failure_reversal": True,
    "lcor_accept_close_atr": 0.05,
    "lcor_spot_hold_tolerance_atr": 0.03,
    "lcor_perp_accept_close_atr": 0.04,
    "lcor_response_flow_ratio": 0.05,
    "lcor_response_close_location": 0.58,
    "lcor_failure_require_spot_flow": True,
    "lcor_failure_require_perp_flow": True,
    "lcor_failure_require_directional_body": True,
    "lcor_stop_buffer_atr": 0.08,
    "lcor_projection_fraction": 1.0,
    "lcor_post_signal_context_bars": 8,
    "lcor_episode_bars": 30,
    "ciot_spot_atr_bars": 20,
    "minimum_structural_rr": 0.75,
}


def make_engine() -> LiquidationCashOwnershipFailureRouterEngine:
    spot = {
        (index + 1) * MINUTE_NS: observation(
            index,
            100.0,
            100.2,
            99.8,
            100.0,
            flow=0.0,
        )
        for index in range(10)
    }
    engine = LiquidationCashOwnershipFailureRouterEngine(
        PARAMS,
        spot_observations=spot,
        metrics={},
    )
    for _ in range(20):
        engine._spot_true_ranges.append(1.0)
    engine._episode = _OwnershipEpisode(
        scenario_id="LCOR-FAILURE-CONTEXT",
        branch=engine.BRANCH,
        side="SELL",
        direction="SHORT",
        state="PERPETUAL_ACCEPTED_CASH_OWNED_AUCTION",
        started_index=0,
        started_ts_ns=MINUTE_NS,
        prior_auction_end_ts_ns=0,
        perp_boundary=100.0,
        spot_boundary=100.0,
        prior_perp_high=105.0,
        prior_perp_low=95.0,
        event_open=101.0,
        event_high=101.0,
        event_low=98.5,
        event_close=99.0,
        event_mid=100.0,
        event_range=2.5,
        event_extreme=98.5,
        atr=1.0,
        baseline_oi=1000.0,
        event_oi=900.0,
        oi_change=-0.10,
        oi_threshold=0.02,
        spot_owner_ts_ns=2 * MINUTE_NS,
        perp_owner_ts_ns=3 * MINUTE_NS,
        high_since_event=101.0,
        low_since_event=98.5,
        inventory_confirmed=True,
        auction_confirmed=True,
        auction_confirmation_index=2,
        auction_confirmation_high=100.0,
        auction_confirmation_low=99.2,
    )
    return engine


# Price, cash flow and perpetual flow must all reject the accepted short relay.
engine = make_engine()
failed_cash = observation(3, 99.9, 100.5, 99.8, 100.4, flow=0.20)
failed_perp = snapshot(3, 99.8, 100.6, 99.7, 100.5, flow=0.20)
step = engine._advance_episode(
    failed_perp,
    failed_cash,
    None,
    allow_new=True,
)
assert step.signal is not None
assert step.signal.family == engine.FAILURE_FAMILY
assert step.signal.direction == "LONG"
assert step.signal.stop_price < step.signal.reference_entry < step.signal.target_price
assert step.signal.scenario_id == "LCOR-FAILURE-CONTEXT:FAILURE_ENTRY"
assert len(step.transitions) == 2
assert step.transitions[0].reason_code == (
    "CASH_AND_PERPETUAL_ACCEPTANCE_FAILED_WITH_OPPOSITE_INITIATIVE"
)
assert step.transitions[1].reason_code == "LCOR_FAILURE_REVERSAL_ENTRY_ARMED"

# Strictly later renewed acceptance in the original short direction invalidates
# the reversal context and can force an open-position exit.
invalidation = engine._advance_episode(
    snapshot(4, 100.1, 100.2, 99.5, 99.6, flow=-0.20),
    observation(4, 100.1, 100.2, 99.4, 99.5, flow=-0.20),
    None,
    allow_new=False,
)
assert invalidation.signal is None
assert len(invalidation.transitions) == 1
assert invalidation.transitions[0].reason_code == engine.FAILURE_INVALIDATION

# A price-only boundary breach is not promoted into a reversal.  With no
# adverse cash flow, the unchanged parent contract simply resets the episode.
neutral = make_engine()
no_flow = neutral._advance_episode(
    failed_perp,
    observation(3, 99.9, 100.5, 99.8, 100.4, flow=0.0),
    None,
    allow_new=True,
)
assert no_flow.signal is None
assert len(no_flow.transitions) == 1
assert no_flow.transitions[0].reason_code == "CASH_AUCTION_LOST_OWNERSHIP_BEFORE_ENTRY"

print("LCOR failed cash-ownership reversal chronology verified")
