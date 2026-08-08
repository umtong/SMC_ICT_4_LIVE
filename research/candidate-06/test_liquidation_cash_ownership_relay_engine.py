#!/usr/bin/env python3
"""Pure causal contracts for the liquidation-to-cash ownership relay."""

from __future__ import annotations

from causal_inventory_ownership_transfer_engine import _OwnershipEpisode
from futures_metrics_data import FuturesMetric
from liquidation_cash_ownership_relay_engine import (
    LiquidationCashOwnershipRelayEngine,
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
        upper_fast=105.0,
        lower_fast=95.0,
        upper_slow=110.0,
        lower_slow=90.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=0,
        lower_pool_touches=0,
    )


def metric(index: int, oi: float) -> FuturesMetric:
    return FuturesMetric(
        ts_ns=(index + 1) * MINUTE_NS,
        open_interest=oi,
        open_interest_value=oi * 100.0,
        top_account_long_short=1.0,
        top_position_long_short=1.0,
        all_account_long_short=1.0,
        taker_buy_sell_ratio=0.70,
    )


PARAMS = {
    "lcor_accept_close_atr": 0.05,
    "lcor_spot_hold_tolerance_atr": 0.03,
    "lcor_forced_removal_retention_fraction": 0.35,
    "lcor_perp_accept_close_atr": 0.04,
    "lcor_response_flow_ratio": 0.05,
    "lcor_response_close_location": 0.58,
    "lcor_retest_band_atr": 0.35,
    "lcor_max_opposing_flow": 0.12,
    "lcor_extension_atr": 0.05,
    "lcor_stop_buffer_atr": 0.08,
    "lcor_projection_fraction": 1.0,
    "lcor_episode_bars": 30,
    "lcor_post_signal_context_bars": 8,
    # Base helpers use the CIOT aliases below.
    "ciot_retest_band_atr": 0.35,
    "ciot_max_opposing_flow": 0.12,
    "ciot_extension_atr": 0.05,
    "ciot_projection_fraction": 1.0,
    "minimum_structural_rr": 0.75,
}

spot = {
    (index + 1) * MINUTE_NS: observation(
        index,
        100.0,
        100.2,
        99.4,
        99.8,
        flow=0.0,
    )
    for index in range(10)
}
engine = LiquidationCashOwnershipRelayEngine(
    PARAMS,
    spot_observations=spot,
    metrics={},
)
for _ in range(20):
    engine._spot_true_ranges.append(1.0)

engine._episode = _OwnershipEpisode(
    scenario_id="LCOR-CONTEXT-SHORT",
    branch=engine.BRANCH,
    side="SELL",
    direction="SHORT",
    state="FORCED_LIQUIDATION_AWAITING_CASH_OWNERSHIP",
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
    spot_owner_ts_ns=None,
    perp_owner_ts_ns=MINUTE_NS,
    high_since_event=101.0,
    low_since_event=98.5,
)

# Later spot acceptance may establish cash ownership, but strict chronology
# prohibits the same bar from also confirming perpetual acceptance.
spot_accept = observation(1, 100.0, 100.1, 99.6, 99.8, flow=-0.10)
accepted = engine._advance_episode(
    snapshot(1, 99.9, 100.0, 99.5, 99.7, flow=-0.20),
    spot_accept,
    metric(1, 930.0),
    allow_new=True,
)
assert accepted.signal is None
assert len(accepted.transitions) == 1
assert accepted.transitions[0].reason_code == (
    "LATER_SPOT_ACCEPTANCE_ASSUMED_OWNERSHIP_AFTER_DELEVERAGING"
)
assert engine._episode is not None
assert engine._episode.spot_owner_ts_ns == spot_accept.ts_ns
assert engine._episode.auction_confirmed is False

# A strictly later completed perpetual bar can accept the cash-owned auction;
# the still-contracted OI state is confirmed at the same causal timestamp.
perp_accept = engine._advance_episode(
    snapshot(2, 99.8, 99.9, 99.2, 99.3, flow=-0.20),
    observation(2, 99.8, 99.9, 99.1, 99.2, flow=-0.10),
    metric(2, 930.0),
    allow_new=True,
)
assert perp_accept.signal is None
assert len(perp_accept.transitions) == 2
reasons = {item.reason_code for item in perp_accept.transitions}
assert "FORCED_OI_REMOVAL_REMAINED_MATERIALLY_RETAINED_AFTER_CASH_ACCEPTANCE" in reasons
assert "PERPETUAL_ACCEPTED_ONLY_AFTER_STRICTLY_EARLIER_CASH_OWNERSHIP" in reasons
assert engine._episode is not None
assert engine._episode.auction_confirmation_index == 2

# The first opposing-flow pullback is a third completed state.
pullback = engine._advance_episode(
    snapshot(3, 99.4, 100.1, 99.6, 99.8, flow=0.05),
    observation(3, 99.5, 100.0, 99.4, 99.8, flow=0.02),
    None,
    allow_new=True,
)
assert pullback.signal is None
assert len(pullback.transitions) == 1
assert pullback.transitions[0].reason_code == (
    "FIRST_OPPOSING_FLOW_PULLBACK_HELD_CASH_OWNED_BOUNDARY"
)

# A fourth completed bar must resume the same initiative before entry arms.
resumption = engine._advance_episode(
    snapshot(4, 99.7, 99.8, 99.1, 99.2, flow=-0.20),
    observation(4, 99.7, 99.8, 99.0, 99.1, flow=-0.10),
    None,
    allow_new=True,
)
assert resumption.signal is not None
assert len(resumption.transitions) == 2
context, entry = resumption.transitions
assert context.scenario_id == "LCOR-CONTEXT-SHORT"
assert context.next_state == "LCOR_SIGNALLED"
assert entry.scenario_id == "LCOR-CONTEXT-SHORT:ENTRY"
assert entry.previous_state == "IDLE"
assert entry.next_state == "ENTRY_ARMED"
assert resumption.signal.scenario_id == entry.scenario_id
assert resumption.signal.family == "LCOR"
assert resumption.signal.details["context_scenario_id"] == "LCOR-CONTEXT-SHORT"
assert resumption.signal.stop_price > resumption.signal.reference_entry
assert resumption.signal.target_price < resumption.signal.reference_entry

# Loss of cash ownership or material re-leveraging declares a causal exit on
# the context namespace and never creates a second signal.
invalidation = engine._advance_episode(
    snapshot(5, 99.5, 100.6, 99.4, 100.4, flow=0.20),
    observation(5, 99.5, 100.7, 99.4, 100.5, flow=0.10),
    metric(5, 980.0),
    allow_new=False,
)
assert invalidation.signal is None
assert len(invalidation.transitions) == 1
assert invalidation.transitions[0].scenario_id == "LCOR-CONTEXT-SHORT"
assert invalidation.transitions[0].reason_code == engine.INVALIDATION

print("LCOR forced-liquidation/cash/perpetual/pullback chronology verified")
