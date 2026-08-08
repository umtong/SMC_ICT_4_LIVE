#!/usr/bin/env python3
"""Causal contracts for the LCOR reaccept-failure reversal router."""

from __future__ import annotations

from math import isclose

from causal_inventory_ownership_transfer_engine import _OwnershipEpisode
from liquidation_cash_ownership_reaccept_failure_engine import (
    LiquidationCashOwnershipReacceptFailureEngine,
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
    obs = observation(
        index,
        open_,
        high,
        low,
        close,
        flow=flow,
    )
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
    "lcor_enable_failure_reversal": False,
    "lcor_enable_reaccept_failure_reversal": True,
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


def make_engine(
    params: dict[str, object] | None = None,
) -> LiquidationCashOwnershipReacceptFailureEngine:
    spot = {
        (index + 1) * MINUTE_NS: observation(
            index,
            100.0,
            100.2,
            99.8,
            100.0,
            flow=0.0,
        )
        for index in range(20)
    }
    engine = LiquidationCashOwnershipReacceptFailureEngine(
        params or PARAMS,
        spot_observations=spot,
        metrics={},
    )
    for _ in range(20):
        engine._spot_true_ranges.append(1.0)
    engine._episode = _OwnershipEpisode(
        scenario_id="LCOR-REACCEPT-FAILURE-CONTEXT",
        branch=engine.BRANCH,
        side="BUY",
        direction="LONG",
        state="PERPETUAL_ACCEPTED_CASH_OWNED_AUCTION",
        started_index=0,
        started_ts_ns=MINUTE_NS,
        prior_auction_end_ts_ns=0,
        perp_boundary=100.0,
        spot_boundary=100.0,
        prior_perp_high=105.0,
        prior_perp_low=95.0,
        event_open=101.0,
        event_high=102.0,
        event_low=98.5,
        event_close=101.5,
        event_mid=101.25,
        event_range=3.5,
        event_extreme=102.0,
        atr=1.0,
        baseline_oi=1000.0,
        event_oi=900.0,
        oi_change=-0.10,
        oi_threshold=0.02,
        spot_owner_ts_ns=2 * MINUTE_NS,
        perp_owner_ts_ns=3 * MINUTE_NS,
        high_since_event=102.0,
        low_since_event=98.5,
        inventory_confirmed=True,
        auction_confirmed=True,
        auction_confirmation_index=2,
        auction_confirmation_high=100.8,
        auction_confirmation_low=99.8,
    )
    return engine


# The first cross-venue failure is context only. It cannot trade.
engine = make_engine()
first_failure = engine._advance_episode(
    snapshot(3, 100.2, 100.3, 99.0, 99.3, flow=-0.20),
    observation(3, 100.1, 100.2, 99.2, 99.4, flow=-0.20),
    None,
    allow_new=True,
)
assert first_failure.signal is None
assert len(first_failure.transitions) == 1
assert first_failure.transitions[0].reason_code == (
    "FIRST_CROSS_VENUE_OWNERSHIP_FAILURE_OBSERVED_"
    "AWAITING_ORIGINAL_REACCEPT"
)
assert engine._episode is not None
assert engine._episode.state == engine.FIRST_FAILURE_WAIT

# A strictly later synchronized original-direction reacceptance starts the
# recovery test. It still cannot trade.
reaccept = engine._advance_episode(
    snapshot(4, 99.4, 100.6, 99.3, 100.3, flow=0.20),
    observation(4, 99.4, 100.5, 99.3, 100.2, flow=0.20),
    None,
    allow_new=True,
)
assert reaccept.signal is None
assert len(reaccept.transitions) == 1
assert reaccept.transitions[0].reason_code == (
    "ORIGINAL_DIRECTION_REACCEPTED_BOTH_BOUNDARIES_"
    "AFTER_FIRST_FAILURE"
)
assert engine._episode is not None
assert engine._episode.state == engine.REACCEPT_TEST

# Only a later second synchronized failure opens the reversal leg. The stop
# is anchored beyond the recovery-test extreme, not the first failure bar.
second_failure = engine._advance_episode(
    snapshot(5, 100.1, 100.1, 99.0, 99.2, flow=-0.30),
    observation(5, 100.1, 100.2, 99.1, 99.3, flow=-0.30),
    None,
    allow_new=True,
)
assert second_failure.signal is not None
assert second_failure.signal.family == engine.REACCEPT_FAILURE_FAMILY
assert second_failure.signal.direction == "SHORT"
assert second_failure.signal.scenario_id == (
    "LCOR-REACCEPT-FAILURE-CONTEXT:REACCEPT_FAILURE_ENTRY"
)
assert second_failure.signal.stop_price > 100.6
assert isclose(second_failure.signal.stop_price, 100.68)
assert (
    second_failure.signal.target_price
    < second_failure.signal.reference_entry
    < second_failure.signal.stop_price
)
assert len(second_failure.transitions) == 2
assert second_failure.transitions[0].reason_code == (
    "REACCEPTED_OWNERSHIP_FAILED_AGAIN_WITH_OPPOSITE_INITIATIVE"
)
assert second_failure.transitions[1].reason_code == (
    "LCOR_REACCEPT_FAILURE_ENTRY_ARMED"
)

# A third original-direction reacceptance after the second-failure signal is a
# causal invalidation and may force an open-position exit.
invalidation = engine._advance_episode(
    snapshot(6, 99.3, 100.5, 99.2, 100.3, flow=0.20),
    observation(6, 99.3, 100.4, 99.2, 100.2, flow=0.20),
    None,
    allow_new=False,
)
assert invalidation.signal is None
assert len(invalidation.transitions) == 1
assert invalidation.transitions[0].reason_code == (
    engine.REACCEPT_FAILURE_INVALIDATION
)

# Without the intervening reacceptance, repeated downside bars never become a
# child entry; the state expires under the existing context horizon.
expired = make_engine()
expired._advance_episode(
    snapshot(3, 100.2, 100.3, 99.0, 99.3, flow=-0.20),
    observation(3, 100.1, 100.2, 99.2, 99.4, flow=-0.20),
    None,
    allow_new=True,
)
terminal = None
for index in range(4, 12):
    terminal = expired._advance_episode(
        snapshot(index, 99.4, 99.6, 98.8, 99.1, flow=-0.20),
        observation(index, 99.4, 99.6, 98.9, 99.2, flow=-0.20),
        None,
        allow_new=True,
    )
assert terminal is not None
assert terminal.signal is None
assert len(terminal.transitions) == 1
assert terminal.transitions[0].reason_code == (
    "FIRST_OWNERSHIP_FAILURE_NOT_REACCEPTED_WITHIN_CONTEXT"
)

print("LCOR reaccept-failure chronology and geometry verified")
