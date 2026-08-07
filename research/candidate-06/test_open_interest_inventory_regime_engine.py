from __future__ import annotations

from futures_metrics_data import FuturesMetric
from lrb_types import BarObservation, PrimitiveSnapshot
from open_interest_inventory_regime_engine import (
    OpenInterestInventoryRegimeRelayEngine,
)

MINUTE = 60 * 1_000_000_000


def metric(ts: int, oi: float, ratio: float) -> FuturesMetric:
    return FuturesMetric(ts, oi, oi * 100.0, 1.0, 1.0, 1.0, ratio)


def snap(
    index: int,
    close: float,
    *,
    open_: float | None = None,
    flow: float = 0.0,
    location: float = 0.5,
    high: float | None = None,
    low: float | None = None,
) -> PrimitiveSnapshot:
    open_ = close if open_ is None else open_
    high = max(open_, close) + 0.2 if high is None else high
    low = min(open_, close) - 0.2 if low is None else low
    volume = 100.0
    taker = volume * (flow + 1.0) / 2.0
    obs = BarObservation(
        index * MINUTE,
        open_,
        high,
        low,
        close,
        volume,
        taker,
        10,
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
        upper_wick_fraction=0.1,
        lower_wick_fraction=0.1,
        close_location=location,
        upper_fast=104.0,
        lower_fast=96.0,
        upper_slow=106.0,
        lower_slow=94.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


def base_params() -> dict[str, object]:
    return {
        "oiir_history_minutes": 1000,
        "oiir_min_prior_changes": 2,
        "oiir_change_quantile": 0.5,
        "oiir_event_move_atr": 0.3,
        "oiir_metric_flow_floor": 0.05,
        "oiir_response_bars": 15,
        "oiir_response_flow_ratio": 0.05,
        "oiir_reclaim_close_location": 0.58,
        "oiir_inventory_retention_fraction": 0.35,
        "oiir_counter_rebuild_fraction": 0.35,
        "oiir_unwind_persistence_fraction": 0.35,
        "oiir_extension_atr": 0.05,
        "oiir_stop_buffer_atr": 0.08,
        "oiir_projection_fraction": 1.0,
        "oiir_cooldown_bars": 2,
        "oiir_invalidation_observation_bars": 6,
        "minimum_structural_rr": 0.5,
        "oiir_enable_build": True,
        "oiir_enable_unwind": True,
        "oiir_enable_unwind_reversal": True,
        "oiir_enable_unwind_continuation": True,
        "oiir_require_counter_inventory_rebuild": True,
    }


def seed_build() -> OpenInterestInventoryRegimeRelayEngine:
    metrics = {
        5 * MINUTE: metric(5 * MINUTE, 100.0, 1.0),
        10 * MINUTE: metric(10 * MINUTE, 101.0, 1.0),
        15 * MINUTE: metric(15 * MINUTE, 103.02, 1.0),
        20 * MINUTE: metric(20 * MINUTE, 108.171, 2.0),
        25 * MINUTE: metric(25 * MINUTE, 107.0, 1.0),
    }
    engine = OpenInterestInventoryRegimeRelayEngine(
        base_params(),
        metrics=metrics,
    )
    event = None
    for index in range(1, 21):
        if index <= 15:
            close = 100.0
            open_ = 100.0
            flow = 0.0
        else:
            open_ = 100.0 + (index - 16) * 0.35
            close = open_ + 0.45
            flow = 0.4
        event = engine.observe(
            snap(index, close, open_=open_, flow=flow, location=0.9),
        )
    assert event is not None
    assert any(
        transition.reason_code
        == "EXTREME_OPEN_INTEREST_EXPANSION_WITH_ALIGNED_PRICE_AND_FLOW"
        for transition in event.transitions
    )
    assert event.signal is None
    return engine


def seed_unwind(
    *,
    rebuild: bool = False,
    persistence: bool = False,
) -> OpenInterestInventoryRegimeRelayEngine:
    metrics = {
        5 * MINUTE: metric(5 * MINUTE, 100.0, 1.0),
        10 * MINUTE: metric(10 * MINUTE, 99.0, 1.0),
        15 * MINUTE: metric(15 * MINUTE, 97.02, 1.0),
        20 * MINUTE: metric(20 * MINUTE, 92.169, 0.5),
    }
    if rebuild:
        metrics[25 * MINUTE] = metric(25 * MINUTE, 95.0, 2.0)
    if persistence:
        metrics[25 * MINUTE] = metric(25 * MINUTE, 90.0, 0.5)
    engine = OpenInterestInventoryRegimeRelayEngine(
        base_params(),
        metrics=metrics,
    )
    event = None
    for index in range(1, 21):
        if index <= 15:
            close = 100.0
            open_ = 100.0
            flow = 0.0
        else:
            open_ = 100.0 - (index - 16) * 0.35
            close = open_ - 0.45
            flow = -0.4
        event = engine.observe(
            snap(index, close, open_=open_, flow=flow, location=0.1),
        )
    assert event is not None
    assert any(
        transition.reason_code
        == "EXTREME_OPEN_INTEREST_CONTRACTION_WITH_ALIGNED_PRICE_AND_FLOW"
        for transition in event.transitions
    )
    assert event.signal is None
    return engine


def assert_entry_namespace(step, family: str) -> None:
    assert step.signal is not None
    assert step.signal.family == family
    assert step.signal.scenario_id.endswith(":ENTRY")
    entry = [
        transition
        for transition in step.transitions
        if transition.event_type == "OIIR_ENTRY_TRANSITION"
    ]
    context = [
        transition
        for transition in step.transitions
        if transition.event_type
        == "OPEN_INTEREST_INVENTORY_REGIME_TRANSITION"
    ]
    assert len(entry) == 1
    assert len(context) == 1
    assert entry[0].scenario_id == step.signal.scenario_id
    assert entry[0].previous_state == "IDLE"
    assert entry[0].next_state == "ENTRY_ARMED"
    assert entry[0].details["context_scenario_id"] == context[0].scenario_id


def main() -> None:
    build = seed_build()
    retained = build.observe(
        snap(25, 102.0, open_=101.8, flow=0.0, location=0.6),
    )
    assert any(
        transition.reason_code == "FRESH_DIRECTIONAL_INVENTORY_REMAINED_OPEN"
        for transition in retained.transitions
    )
    pullback = build.observe(
        snap(
            26,
            101.25,
            open_=102.0,
            flow=-0.4,
            location=0.6,
            high=102.1,
            low=100.95,
        ),
    )
    assert any(
        transition.reason_code
        == "FIRST_OPPOSING_FLOW_PULLBACK_HELD_INVENTORY_VALUE"
        for transition in pullback.transitions
    )
    resumed = build.observe(
        snap(
            27,
            102.35,
            open_=101.4,
            flow=0.4,
            location=0.9,
            high=102.5,
            low=101.3,
        ),
    )
    assert_entry_namespace(resumed, "OIIR_B")
    assert resumed.signal.direction == "LONG"

    unwind_reversal = seed_unwind(rebuild=True)
    for index in range(21, 25):
        step = unwind_reversal.observe(
            snap(
                index,
                97.0,
                open_=96.8,
                flow=0.1,
                location=0.6,
            ),
        )
        assert step.signal is None
    reversal = unwind_reversal.observe(
        snap(
            25,
            99.5,
            open_=97.0,
            flow=0.5,
            location=0.9,
        ),
    )
    assert_entry_namespace(reversal, "OIIR_UR")
    assert reversal.signal.direction == "LONG"

    unwind_continuation = seed_unwind(persistence=True)
    for index in range(21, 25):
        step = unwind_continuation.observe(
            snap(
                index,
                97.0 - 0.2 * (index - 21),
                open_=97.2,
                flow=-0.3,
                location=0.1,
            ),
        )
        assert step.signal is None
    continuation = unwind_continuation.observe(
        snap(
            25,
            95.6,
            open_=96.2,
            flow=-0.5,
            location=0.1,
        ),
    )
    assert_entry_namespace(continuation, "OIIR_UC")
    assert continuation.signal.direction == "SHORT"

    ablation_params = base_params()
    ablation_params["oiir_require_counter_inventory_rebuild"] = False
    ablation = seed_unwind()
    ablation.params.update(ablation_params)
    price_only = ablation.observe(
        snap(
            21,
            99.5,
            open_=97.0,
            flow=0.5,
            location=0.9,
        ),
    )
    assert_entry_namespace(price_only, "OIIR_UR")
    print("OIIR state-machine and ledger contracts passed")


if __name__ == "__main__":
    main()
