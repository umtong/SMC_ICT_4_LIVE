#!/usr/bin/env python3
from __future__ import annotations

from lrb_types import BarObservation, PrimitiveSnapshot
from open_interest_contraction_engine import OpenInterestContractionBifurcationEngine
from open_interest_metrics_data import OpenInterestPoint

MINUTE = 60_000_000_000


def snap(
    index: int,
    close: float,
    *,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    flow: float = 0.0,
) -> PrimitiveSnapshot:
    open_value = close if open_ is None else open_
    high_value = max(open_value, close) if high is None else high
    low_value = min(open_value, close) if low is None else low
    location = 0.5 if high_value <= low_value else (close - low_value) / (high_value - low_value)
    observation = BarObservation(
        ts_ns=(index + 1) * MINUTE,
        open=open_value,
        high=high_value,
        low=low_value,
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
        body_atr=abs(close - open_value),
        range_atr=high_value - low_value,
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


oi: dict[int, OpenInterestPoint] = {}
value = 1000.0
for point_index in range(9):
    ts_ns = (point_index * 5 + 1) * MINUTE
    value *= 0.995 if point_index == 8 else 0.9999
    oi[ts_ns] = OpenInterestPoint(ts_ns, value, None, None)

params = {
    "oicb_history_points": 8,
    "oicb_minimum_history": 4,
    "oicb_minimum_drop_fraction": 0.001,
    "oicb_score_floor": 1.0,
    "oicb_price_move_atr": 0.5,
    "oicb_flow_floor": 0.02,
    "oicb_response_bars": 8,
    "oicb_response_body_atr": 0.15,
    "oicb_response_flow_ratio": 0.02,
    "oicb_response_close_location": 0.6,
    "oicb_stop_buffer_atr": 0.05,
    "oicb_projection_fraction": 0.75,
    "minimum_structural_rr": 0.5,
    "oicb_use_open_interest": True,
    "oicb_require_aligned_flow": True,
}
engine = OpenInterestContractionBifurcationEngine(params, open_interest=oi)
for index in range(41):
    close = 100.0 if index < 36 else 100.0 + 0.25 * (index - 35)
    step = engine.observe(
        snap(
            index,
            close,
            open_=close - 0.1,
            high=close + 0.1,
            low=close - 0.15,
            flow=0.15,
        ),
        allow_new=True,
    )
    assert step.signal is None
step = engine.observe(
    snap(41, 100.65, open_=100.9, high=101.0, low=100.55, flow=-0.20),
    allow_new=True,
)
assert step.signal is not None
assert step.signal.family == "OICB_E"
assert step.signal.direction == "SHORT"

params_no_oi = dict(params)
params_no_oi["oicb_use_open_interest"] = False
engine_no_oi = OpenInterestContractionBifurcationEngine(params_no_oi, open_interest=oi)
for index in range(4):
    step = engine_no_oi.observe(snap(index, 100.0 + index, flow=0.2), allow_new=True)
    assert step.signal is None
print("open-interest contraction state-machine contract passed")
