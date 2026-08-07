#!/usr/bin/env python3
from __future__ import annotations

from lrb_types import BarObservation, PrimitiveSnapshot
from volume_clock_impact_engine import VolumeClockImpactBifurcationEngine

MINUTE = 60_000_000_000


def snapshot(index: int, open_: float, close: float, *, volume: float = 100.0, flow: float = 0.0) -> PrimitiveSnapshot:
    high = max(open_, close) + 0.05
    low = min(open_, close) - 0.05
    location = (close - low) / (high - low)
    observation = BarObservation(
        ts_ns=(index + 1) * MINUTE,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=0.5 * volume * (flow + 1.0),
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
    "vcib_volume_lookback": 10,
    "vcib_minimum_volume_history": 5,
    "vcib_target_minutes": 2.0,
    "vcib_flow_floor": 0.10,
    "vcib_displacement_atr": 0.20,
    "vcib_close_location": 0.60,
    "vcib_efficiency_history": 10,
    "vcib_minimum_efficiency_history": 2,
    "vcib_continuation_quantile": 0.50,
    "vcib_exhaustion_quantile": 0.25,
    "vcib_response_bars": 8,
    "vcib_response_body_atr": 0.10,
    "vcib_response_flow_ratio": 0.02,
    "vcib_response_close_location": 0.60,
    "vcib_stop_buffer_atr": 0.05,
    "vcib_projection_fraction": 0.75,
    "minimum_structural_rr": 0.50,
    "vcib_use_impact_efficiency": True,
}
engine = VolumeClockImpactBifurcationEngine(params)
# Prior volume history cannot include the current minute before bucket budget creation.
for index in range(5):
    step = engine.observe(snapshot(index, 100.0, 100.0, volume=100.0), allow_new=True)
    assert step.signal is None
assert engine._bucket_start_index is None
# The sixth completed minute starts the first causal bucket from the five prior volumes.
engine.observe(snapshot(5, 100.0, 100.2, volume=100.0, flow=0.2), allow_new=True)
assert engine._bucket_start_index == 5
assert engine._bucket_budget == 200.0
print("volume-clock prior-only threshold contract passed")
