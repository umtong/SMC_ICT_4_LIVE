#!/usr/bin/env python3
from __future__ import annotations

from lrb_types import BarObservation, PrimitiveSnapshot
from sequential_pressure_live_engine import SequentialPressureLiveEngine
from sequential_pressure_regime_engine import _FlowState

MINUTE = 60_000_000_000


def snap(index: int, open_: float, close: float, flow: float) -> PrimitiveSnapshot:
    high = max(open_, close) + 0.05
    low = min(open_, close) - 0.05
    location = (close - low) / (high - low)
    observation = BarObservation(
        ts_ns=(index + 1) * MINUTE,
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
        close_location=location,
        upper_fast=None,
        lower_fast=None,
        upper_slow=None,
        lower_slow=None,
        slow_mid=None,
        range_position=None,
        upper_pool_touches=0,
        lower_pool_touches=0,
    )

params = {
    "sprc_resume_z": 0.5,
    "sprc_response_body_atr": 0.1,
    "sprc_response_close_location": 0.6,
    "sprc_stop_buffer_atr": 0.05,
    "sprc_projection_fraction": 1.0,
    "minimum_structural_rr": 0.5,
    "sprc_exit_cusum_drift": 0.0,
    "sprc_exit_threshold": 0.5,
    "sprc_max_regime_bars": 30,
}
engine = SequentialPressureLiveEngine(params)
engine._state = _FlowState(
    scenario_id="SPRC-TEST",
    direction="LONG",
    state="PULLBACK_HELD",
    created_index=0,
    created_ts_ns=MINUTE,
    origin=99.0,
    onset_close=100.0,
    midpoint=99.5,
    extreme=100.2,
    atr=1.0,
    onset_score=5.0,
    pullback_index=1,
    pullback_extreme=99.7,
)
step = engine._advance(snap(2, 100.0, 100.4, 1.0), 1.0, allow_new=True)
assert step.signal is not None
assert engine._state is not None
assert engine._state.state == "POSITION_CONTEXT"
exit_step = engine._advance(snap(3, 100.4, 100.2, -1.0), -1.0, allow_new=False)
assert exit_step.signal is None
assert any(
    transition.reason_code == "PRESSURE_REGIME_TERMINATED_BY_OPPOSITE_CUSUM"
    for transition in exit_step.transitions
)
assert engine._state is None
print("SPRC live-context and causal-exit contracts passed")
