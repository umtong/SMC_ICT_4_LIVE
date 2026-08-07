#!/usr/bin/env python3
"""Causal state tests for CIRB discharge and counter-inventory branches."""

from __future__ import annotations

from crowding_inventory_response_engine import (
    CrowdingInventoryResponseBifurcationEngine,
)
from futures_metrics_data import FuturesMetric
from lrb_types import BarObservation, PrimitiveSnapshot

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
    location = (
        0.5
        if high_value <= low_value
        else (close - low_value) / (high_value - low_value)
    )
    volume = 100.0
    observation = BarObservation(
        ts_ns=(index + 1) * MINUTE,
        open=open_value,
        high=high_value,
        low=low_value,
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
        body_atr=abs(close - open_value),
        range_atr=high_value - low_value,
        upper_wick_fraction=0.0,
        lower_wick_fraction=0.0,
        close_location=location,
        upper_fast=103.0,
        lower_fast=97.0,
        upper_slow=104.0,
        lower_slow=96.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=0,
        lower_pool_touches=0,
    )


def metric(
    index: int,
    oi: float,
    *,
    all_ratio: float,
    taker: float,
) -> FuturesMetric:
    return FuturesMetric(
        ts_ns=(index + 1) * MINUTE,
        open_interest=oi,
        open_interest_value=oi * 100.0,
        top_account_long_short=all_ratio * 1.05,
        top_position_long_short=all_ratio * 1.10,
        all_account_long_short=all_ratio,
        taker_buy_sell_ratio=taker,
    )


PARAMS = {
    "oidb_history_minutes": 120,
    "oidb_min_prior_drops": 4,
    "oidb_drop_quantile": 0.5,
    "oidb_event_move_atr": 0.30,
    "oidb_metric_flow_floor": 0.04,
    "oidb_response_bars": 6,
    "oidb_response_flow_ratio": 0.04,
    "oidb_reclaim_close_location": 0.58,
    "oidb_persistence_fraction": 0.35,
    "oidb_extension_atr": 0.05,
    "oidb_stop_buffer_atr": 0.08,
    "oidb_projection_fraction": 1.0,
    "oidb_cooldown_bars": 2,
    "oidb_invalidation_observation_bars": 6,
    "oidb_use_open_interest": True,
    "oidb_enable_reversal": True,
    "oidb_enable_continuation": True,
    "cirb_use_all_account_composition": True,
    "cirb_enable_discharge_response": True,
    "cirb_enable_counter_inventory_continuation": True,
    "cirb_counter_response_bars": 15,
    "cirb_counter_rebuild_fraction": 0.35,
    "cirb_require_counter_composition_persistence": True,
    "minimum_structural_rr": 0.50,
}


def seed_metrics(event_all_ratio: float) -> dict[int, FuturesMetric]:
    observations: dict[int, FuturesMetric] = {}
    oi = 1000.0
    ratio = 1.0
    for index in (0, 5, 10, 15, 20, 25):
        oi *= 0.999
        ratio *= 1.001
        observations[(index + 1) * MINUTE] = metric(
            index,
            oi,
            all_ratio=ratio,
            taker=1.0,
        )
    observations[31 * MINUTE] = metric(
        30,
        oi * 0.992,
        all_ratio=event_all_ratio,
        taker=1.40,
    )
    return observations


# DISCHARGE: an up shock with all-account composition moving up may later reclaim
# down and arm a reversal. The initiating event itself must not emit a signal.
discharge_metrics = seed_metrics(event_all_ratio=1.08)
discharge = CrowdingInventoryResponseBifurcationEngine(
    PARAMS,
    metrics=discharge_metrics,
)
for index in range(31):
    close = 100.0 if index < 26 else 100.0 + 0.25 * (index - 25)
    step = discharge.observe(
        snap(
            index,
            close,
            open_=close - 0.1,
            high=close + 0.15,
            low=close - 0.15,
            flow=0.15,
        ),
        allow_new=True,
    )
    assert step.signal is None
assert any(
    transition.reason_code
    == "EXTREME_OI_CONTRACTION_WITH_ALIGNED_FLOW_AND_CROWD_DISCHARGE"
    for transition in step.transitions
)
step = discharge.observe(
    snap(31, 100.55, open_=100.9, high=101.0, low=100.45, flow=-0.20),
    allow_new=True,
)
assert step.signal is not None
assert step.signal.family == "CIRB_D_R"
assert step.signal.direction == "SHORT"
assert step.signal.scenario_id.endswith(":ENTRY")

# COUNTER_INVENTORY: an up shock with the account ratio moving down cannot
# reverse. A later completed metric must rebuild OI, retain the opposite account
# composition and accompany a fresh extension before continuation is armed.
counter_metrics = seed_metrics(event_all_ratio=0.92)
event_oi = counter_metrics[31 * MINUTE].open_interest
counter_metrics[36 * MINUTE] = metric(
    35,
    event_oi * 1.004,
    all_ratio=0.90,
    taker=1.50,
)
counter = CrowdingInventoryResponseBifurcationEngine(
    PARAMS,
    metrics=counter_metrics,
)
for index in range(31):
    close = 100.0 if index < 26 else 100.0 + 0.25 * (index - 25)
    step = counter.observe(
        snap(
            index,
            close,
            open_=close - 0.1,
            high=close + 0.15,
            low=close - 0.15,
            flow=0.15,
        ),
        allow_new=True,
    )
    assert step.signal is None
assert any(
    transition.reason_code
    == "EXTREME_OI_CONTRACTION_WITH_ALIGNED_FLOW_AND_COUNTER_INVENTORY"
    for transition in step.transitions
)
for index in range(31, 35):
    close = 101.30 + 0.03 * (index - 31)
    step = counter.observe(
        snap(
            index,
            close,
            open_=close - 0.02,
            high=close + 0.04,
            low=close - 0.04,
            flow=0.10,
        ),
        allow_new=True,
    )
    assert step.signal is None
step = counter.observe(
    snap(35, 101.85, open_=101.45, high=101.9, low=101.4, flow=0.25),
    allow_new=True,
)
assert step.signal is not None
assert step.signal.family == "CIRB_T_C"
assert step.signal.direction == "LONG"
assert step.signal.details["counter_inventory_rebuild_fraction"] > 0.0

print("crowding inventory-response causal state-machine contract passed")
