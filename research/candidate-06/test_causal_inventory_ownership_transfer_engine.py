#!/usr/bin/env python3
"""Pure causal contracts for CIOT ownership transfer and entry arming."""

from __future__ import annotations

from causal_inventory_ownership_transfer_engine import (
    CausalInventoryOwnershipTransferEngine,
    _OwnershipEpisode,
)
from futures_metrics_data import FuturesMetric
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
        taker_buy_sell_ratio=1.5,
    )


PARAMS = {
    "ciot_counter_rebuild_fraction": 0.35,
    "ciot_inventory_retention_fraction": 0.35,
    "ciot_response_flow_ratio": 0.05,
    "ciot_response_close_location": 0.58,
    "ciot_retest_band_atr": 0.35,
    "ciot_max_opposing_flow": 0.12,
    "ciot_extension_atr": 0.05,
    "ciot_stop_buffer_atr": 0.08,
    "ciot_projection_fraction": 1.0,
    "ciot_episode_bars": 30,
    "ciot_post_signal_context_bars": 8,
    "ciot_spot_atr_bars": 20,
    "minimum_structural_rr": 0.75,
}

spot = {
    (index + 1) * MINUTE_NS: observation(
        index,
        100.0,
        100.5,
        99.8,
        100.2,
        flow=0.0,
    )
    for index in range(10)
}
metrics = {
    2 * MINUTE_NS: metric(1, 940.0),
    5 * MINUTE_NS: metric(4, 890.0),
}
engine = CausalInventoryOwnershipTransferEngine(
    PARAMS,
    spot_observations=spot,
    metrics=metrics,
)
for _ in range(20):
    engine._spot_true_ranges.append(1.0)

engine._episode = _OwnershipEpisode(
    scenario_id="CIOT-CONTEXT-R",
    branch="REVERSAL",
    side="SELL",
    direction="LONG",
    state="FORCED_INVENTORY_REMOVAL_WITH_SPOT_REFUSAL",
    started_index=0,
    started_ts_ns=MINUTE_NS,
    prior_auction_end_ts_ns=0,
    perp_boundary=100.0,
    spot_boundary=100.0,
    prior_perp_high=105.0,
    prior_perp_low=95.0,
    event_open=101.0,
    event_high=101.0,
    event_low=98.0,
    event_close=99.0,
    event_mid=100.0,
    event_range=3.0,
    event_extreme=98.0,
    atr=1.0,
    baseline_oi=1000.0,
    event_oi=900.0,
    oi_change=-0.10,
    oi_threshold=0.02,
    spot_owner_ts_ns=None,
    perp_owner_ts_ns=MINUTE_NS,
    high_since_event=101.0,
    low_since_event=98.0,
)

# One later completed bar may confirm OI rebuilding and invalidate the old
# auction, but it may not also become the pullback or entry bar.
confirmation = engine._advance_episode(
    snapshot(1, 99.5, 100.7, 99.4, 100.5, flow=0.20),
    spot[2 * MINUTE_NS],
    metrics[2 * MINUTE_NS],
    allow_new=True,
)
assert confirmation.signal is None
assert len(confirmation.transitions) == 2
assert confirmation.transitions[0].reason_code == "COUNTER_INVENTORY_REBUILT_AFTER_FORCED_REMOVAL"
assert confirmation.transitions[1].reason_code == "OLD_INVENTORY_AUCTION_INVALIDATED"
assert engine._episode is not None
assert engine._episode.auction_confirmation_index == 1

# A distinct opposing-flow pullback must hold the new owner boundary.
pullback = engine._advance_episode(
    snapshot(2, 100.5, 100.6, 99.9, 100.2, flow=-0.05),
    spot[3 * MINUTE_NS],
    None,
    allow_new=True,
)
assert pullback.signal is None
assert len(pullback.transitions) == 1
assert pullback.transitions[0].reason_code == "FIRST_OPPOSING_FLOW_PULLBACK_HELD_NEW_OWNER_BOUNDARY"

# Only a third completed bar may resume the initiative and arm execution.
resumption = engine._advance_episode(
    snapshot(3, 100.2, 100.9, 100.1, 100.8, flow=0.20),
    spot[4 * MINUTE_NS],
    None,
    allow_new=True,
)
assert resumption.signal is not None
assert len(resumption.transitions) == 2
context, entry = resumption.transitions
assert context.scenario_id == "CIOT-CONTEXT-R"
assert context.next_state == "CIOT_R_SIGNALLED"
assert entry.scenario_id == "CIOT-CONTEXT-R:ENTRY"
assert entry.previous_state == "IDLE"
assert entry.next_state == "ENTRY_ARMED"
assert resumption.signal.scenario_id == entry.scenario_id
assert resumption.signal.details["context_scenario_id"] == "CIOT-CONTEXT-R"
assert resumption.signal.family == "CIOT_R"
assert resumption.signal.stop_price < 99.9
assert resumption.signal.target_price > resumption.signal.reference_entry
assert resumption.signal.target_reason != "TARGET_ALREADY_CONSUMED"

# A later loss of both cash-auction ownership and rebuilt inventory produces
# the scenario-declared causal exit, never a second entry.
invalid_spot = observation(4, 100.0, 100.1, 98.8, 99.0, flow=-0.20)
invalidation = engine._advance_episode(
    snapshot(4, 100.0, 100.1, 98.8, 99.0, flow=-0.20),
    invalid_spot,
    metrics[5 * MINUTE_NS],
    allow_new=False,
)
assert invalidation.signal is None
assert len(invalidation.transitions) == 1
assert invalidation.transitions[0].scenario_id == "CIOT-CONTEXT-R"
assert invalidation.transitions[0].reason_code == "COUNTER_INVENTORY_OWNERSHIP_TRANSFER_INVALIDATED"

# The two attribution ablations are explicit configuration switches; neither
# rewrites the stop, target, risk, fill, or timing contract.
ablation = CausalInventoryOwnershipTransferEngine(
    {
        **PARAMS,
        "ciot_require_spot_ownership": False,
        "ciot_require_inventory_confirmation": False,
    },
    spot_observations=spot,
    metrics=metrics,
)
for _ in range(20):
    ablation._spot_true_ranges.append(1.0)
assert ablation.params["ciot_require_spot_ownership"] is False
assert ablation.params["ciot_require_inventory_confirmation"] is False

print("CIOT ownership chronology, separate entry namespace and causal exit verified")
