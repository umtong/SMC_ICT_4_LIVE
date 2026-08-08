#!/usr/bin/env python3
"""Contract test for SPRC context, live context, and entry namespaces."""

from __future__ import annotations

from lrb_types import BarObservation, PrimitiveSnapshot
from sequential_pressure_live_engine import SequentialPressureLiveEngine
from sequential_pressure_regime_engine import SequentialPressureRegimeEngine, _FlowState

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


def make_state(scenario_id: str) -> _FlowState:
    return _FlowState(
        scenario_id=scenario_id,
        direction="LONG",
        state="PULLBACK_HELD",
        created_index=0,
        created_ts_ns=MINUTE_NS,
        origin=100.0,
        onset_close=101.0,
        midpoint=100.5,
        extreme=102.0,
        atr=1.0,
        onset_score=5.0,
        pullback_index=1,
        pullback_extreme=100.8,
    )


PARAMS = {
    "sprc_max_regime_bars": 30,
    "sprc_exit_cusum_drift": 0.20,
    "sprc_exit_threshold": 3.5,
    "sprc_resume_z": 0.50,
    "sprc_response_body_atr": 0.15,
    "sprc_response_close_location": 0.62,
    "sprc_stop_buffer_atr": 0.08,
    "sprc_projection_fraction": 0.75,
    "sprc_cooldown_bars": 2,
    "minimum_structural_rr": 0.75,
}
response = make_snapshot(2, 101.1, 101.6, flow=0.40)

# Base detector: market context and executable signal have different IDs.
base = SequentialPressureRegimeEngine(PARAMS)
base._state = make_state("SPRC-CONTEXT-BASE")
base_step = base._advance(response, 1.0, allow_new=True)
assert base_step.signal is not None
assert len(base_step.transitions) == 2
context_confirmation, entry_armed = base_step.transitions
assert context_confirmation.scenario_id == "SPRC-CONTEXT-BASE"
assert context_confirmation.previous_state == "PULLBACK_HELD"
assert context_confirmation.next_state == "CONTINUATION_CONFIRMED"
assert entry_armed.scenario_id == "SPRC-CONTEXT-BASE:ENTRY"
assert entry_armed.previous_state == "IDLE"
assert entry_armed.next_state == "ENTRY_ARMED"
assert entry_armed.details["context_scenario_id"] == "SPRC-CONTEXT-BASE"
assert base_step.signal.scenario_id == entry_armed.scenario_id
assert base_step.signal.details["context_scenario_id"] == "SPRC-CONTEXT-BASE"

# Live detector: the confirmed context is explicitly preserved for exit monitoring.
live = SequentialPressureLiveEngine(PARAMS)
live._state = make_state("SPRC-CONTEXT-LIVE")
live_step = live._advance(response, 1.0, allow_new=True)
assert live_step.signal is not None
assert len(live_step.transitions) == 3
confirmation, position_context, live_entry = live_step.transitions
assert confirmation.scenario_id == "SPRC-CONTEXT-LIVE"
assert confirmation.next_state == "CONTINUATION_CONFIRMED"
assert position_context.scenario_id == "SPRC-CONTEXT-LIVE"
assert position_context.previous_state == "CONTINUATION_CONFIRMED"
assert position_context.next_state == "POSITION_CONTEXT"
assert live_entry.scenario_id == "SPRC-CONTEXT-LIVE:ENTRY"
assert live_entry.previous_state == "IDLE"
assert live_entry.next_state == "ENTRY_ARMED"
assert live._state is not None
assert live._state.state == "POSITION_CONTEXT"

# Later invalidation must continue the logged context chain, not the entry chain.
reset_step = live._advance(
    make_snapshot(3, 100.1, 99.9, flow=-0.20),
    0.0,
    allow_new=False,
)
assert len(reset_step.transitions) == 1
reset = reset_step.transitions[0]
assert reset.scenario_id == "SPRC-CONTEXT-LIVE"
assert reset.previous_state == "POSITION_CONTEXT"
assert reset.next_state == "RESET"

# Context-only observation cannot create an executable entry state.
context_only = SequentialPressureRegimeEngine(PARAMS)
context_only._state = make_state("SPRC-CONTEXT-ONLY")
context_step = context_only._advance(response, 1.0, allow_new=False)
assert context_step.signal is None
assert len(context_step.transitions) == 1
assert context_step.transitions[0].scenario_id == "SPRC-CONTEXT-ONLY"
assert context_step.transitions[0].next_state == "CONTINUATION_CONFIRMED"

print("SPRC context, live context, and entry namespaces are causally separated")
