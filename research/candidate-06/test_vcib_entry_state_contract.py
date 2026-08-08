#!/usr/bin/env python3
"""Contract test for VCIB context confirmation and execution arming."""

from __future__ import annotations

from lrb_types import BarObservation, PrimitiveSnapshot
from volume_clock_impact_engine import (
    VolumeClockImpactBifurcationEngine,
    _Bucket,
    _Episode,
)

MINUTE_NS = 60_000_000_000


def make_snapshot(
    index: int,
    open_: float,
    close: float,
    *,
    flow: float,
) -> PrimitiveSnapshot:
    high = max(open_, close) + 0.05
    low = min(open_, close) - 0.05
    observation = BarObservation(
        ts_ns=(index + 1) * MINUTE_NS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=50.0 * (flow + 1.0),
        trades=10,
    )
    return PrimitiveSnapshot(
        index=index,
        observation=observation,
        ready=True,
        atr=1.0,
        rel_volume=1.0,
        flow_ratio=flow,
        body_atr=abs(close - open_),
        range_atr=high - low,
        upper_wick_fraction=0.0,
        lower_wick_fraction=0.0,
        close_location=(close - low) / (high - low),
        upper_fast=None,
        lower_fast=None,
        upper_slow=None,
        lower_slow=None,
        slow_mid=None,
        range_position=None,
        upper_pool_touches=0,
        lower_pool_touches=0,
    )


def make_bucket(
    start_index: int,
    end_index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    direction: str,
    flow: float,
    efficiency: float,
) -> _Bucket:
    return _Bucket(
        start_index=start_index,
        end_index=end_index,
        start_ts_ns=(start_index + 1) * MINUTE_NS,
        end_ts_ns=(end_index + 1) * MINUTE_NS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=200.0,
        signed_volume=flow * 200.0,
        atr=1.0,
        close_location=(close - low) / (high - low),
        direction=direction,
        flow_ratio=flow,
        displacement_atr=abs(close - open_),
        efficiency=efficiency,
    )


PARAMS = {
    "vcib_response_bars": 8,
    "vcib_response_body_atr": 0.10,
    "vcib_response_flow_ratio": 0.02,
    "vcib_response_close_location": 0.60,
    "vcib_stop_buffer_atr": 0.05,
    "vcib_projection_fraction": 0.75,
    "minimum_structural_rr": 0.50,
    "vcib_cooldown_bars": 2,
}


def assert_entry_contract(step, context_id: str, family: str) -> None:
    assert step.signal is not None
    assert len(step.transitions) == 2
    context_transition, entry_transition = step.transitions
    assert context_transition.scenario_id == context_id
    assert context_transition.next_state.endswith("CONFIRMED")
    assert entry_transition.scenario_id == f"{context_id}:ENTRY"
    assert entry_transition.previous_state == "IDLE"
    assert entry_transition.next_state == "ENTRY_ARMED"
    assert entry_transition.details["context_scenario_id"] == context_id
    assert step.signal.scenario_id == entry_transition.scenario_id
    assert step.signal.family == family
    assert step.signal.details["context_scenario_id"] == context_id


# Continuation: the context confirms only after a distinct retest and response.
continuation = VolumeClockImpactBifurcationEngine(PARAMS)
continuation._episode = _Episode(
    scenario_id="VCIB-CONTEXT-C",
    state="CONTINUATION_RETEST",
    direction="UP",
    first=make_bucket(
        0,
        0,
        open_=100.0,
        high=102.0,
        low=100.0,
        close=101.5,
        direction="UP",
        flow=0.30,
        efficiency=1.00,
    ),
    second=make_bucket(
        1,
        1,
        open_=101.5,
        high=103.0,
        low=101.0,
        close=102.5,
        direction="UP",
        flow=0.35,
        efficiency=1.10,
    ),
    created_index=0,
    retest_index=1,
    retest_extreme=101.6,
)
continuation_step = continuation._advance_episode(
    make_snapshot(2, 102.0, 102.6, flow=0.40),
    allow_new=True,
)
assert_entry_contract(continuation_step, "VCIB-CONTEXT-C", "VCIB_C")

# Exhaustion: failed extension confirms only after a later opposite response.
exhaustion = VolumeClockImpactBifurcationEngine(PARAMS)
exhaustion._episode = _Episode(
    scenario_id="VCIB-CONTEXT-E",
    state="EXHAUSTION_CONTEXT",
    direction="UP",
    first=make_bucket(
        0,
        0,
        open_=100.0,
        high=102.0,
        low=100.0,
        close=101.5,
        direction="UP",
        flow=0.30,
        efficiency=1.00,
    ),
    second=make_bucket(
        1,
        1,
        open_=101.5,
        high=101.9,
        low=100.8,
        close=101.7,
        direction="UP",
        flow=0.25,
        efficiency=0.20,
    ),
    created_index=0,
)
exhaustion_step = exhaustion._advance_episode(
    make_snapshot(2, 101.4, 100.8, flow=-0.40),
    allow_new=True,
)
assert_entry_contract(exhaustion_step, "VCIB-CONTEXT-E", "VCIB_E")

# Context-only observation must never create an entry namespace.
context_only = VolumeClockImpactBifurcationEngine(PARAMS)
context_only._episode = _Episode(
    scenario_id="VCIB-CONTEXT-ONLY",
    state="EXHAUSTION_CONTEXT",
    direction="UP",
    first=exhaustion_step.signal and exhaustion._entry_scenario_id  # type: ignore[assignment]
    if False
    else make_bucket(
        0,
        0,
        open_=100.0,
        high=102.0,
        low=100.0,
        close=101.5,
        direction="UP",
        flow=0.30,
        efficiency=1.00,
    ),
    second=make_bucket(
        1,
        1,
        open_=101.5,
        high=101.9,
        low=100.8,
        close=101.7,
        direction="UP",
        flow=0.25,
        efficiency=0.20,
    ),
    created_index=0,
)
context_step = context_only._advance_episode(
    make_snapshot(2, 101.4, 100.8, flow=-0.40),
    allow_new=False,
)
assert context_step.signal is None
assert len(context_step.transitions) == 1
assert context_step.transitions[0].scenario_id == "VCIB-CONTEXT-ONLY"

print("VCIB context and entry namespaces are causally separated")
