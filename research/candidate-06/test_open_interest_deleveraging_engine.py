from __future__ import annotations

from futures_metrics_data import FuturesMetric
from lrb_types import BarObservation, PrimitiveSnapshot
from open_interest_deleveraging_engine import OpenInterestDeleveragingBifurcationEngine

MINUTE = 60 * 1_000_000_000


def metric(ts: int, oi: float, ratio: float) -> FuturesMetric:
    return FuturesMetric(ts, oi, oi * 100.0, 1.0, 1.0, 1.0, ratio)


def snap(index: int, close: float, *, open_: float | None = None, flow: float = 0.0, location: float = 0.5) -> PrimitiveSnapshot:
    open_ = close if open_ is None else open_
    high = max(open_, close) + 0.2
    low = min(open_, close) - 0.2
    volume = 100.0
    taker = volume * (flow + 1.0) / 2.0
    obs = BarObservation(index * MINUTE, open_, high, low, close, volume, taker, 10)
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
        upper_fast=103.0,
        lower_fast=97.0,
        upper_slow=105.0,
        lower_slow=95.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


def base_params() -> dict[str, object]:
    return {
        "oidb_history_minutes": 1000,
        "oidb_min_prior_drops": 2,
        "oidb_drop_quantile": 0.5,
        "oidb_event_move_atr": 0.3,
        "oidb_metric_flow_floor": 0.05,
        "oidb_response_bars": 6,
        "oidb_response_flow_ratio": 0.05,
        "oidb_reclaim_close_location": 0.58,
        "oidb_persistence_fraction": 0.35,
        "oidb_extension_atr": 0.05,
        "oidb_stop_buffer_atr": 0.08,
        "oidb_projection_fraction": 1.0,
        "minimum_structural_rr": 0.5,
        "oidb_enable_reversal": True,
        "oidb_enable_continuation": True,
        "oidb_use_open_interest": True,
    }


def seed_and_event(*, continuation: bool = False):
    # Metrics at completed 5m boundaries. Prior drops are 1% and 2%; event is 5%.
    metrics = {
        5 * MINUTE: metric(5 * MINUTE, 100.0, 1.0),
        10 * MINUTE: metric(10 * MINUTE, 99.0, 1.0),
        15 * MINUTE: metric(15 * MINUTE, 97.02, 1.0),
        20 * MINUTE: metric(20 * MINUTE, 92.169, 0.5),
    }
    if continuation:
        metrics[25 * MINUTE] = metric(25 * MINUTE, 90.0, 0.5)
    engine = OpenInterestDeleveragingBifurcationEngine(base_params(), metrics=metrics)
    event_step = None
    for i in range(1, 21):
        if i <= 15:
            close = 100.0
            open_ = 100.0
            flow = 0.0
        else:
            open_ = 100.0 - (i - 16) * 0.25
            close = open_ - 0.4
            flow = -0.4
        event_step = engine.observe(snap(i, close, open_=open_, flow=flow, location=0.1))
    assert event_step is not None
    assert any(t.reason_code == "EXTREME_OPEN_INTEREST_CONTRACTION_WITH_ALIGNED_FLOW" for t in event_step.transitions)
    assert event_step.signal is None, "same-bar entry is forbidden"
    return engine


def main() -> None:
    engine = seed_and_event()
    reversal = engine.observe(snap(21, 99.8, open_=98.0, flow=0.4, location=0.9))
    assert reversal.signal is not None
    assert reversal.signal.family == "OIDB_R"
    assert reversal.signal.direction == "LONG"

    engine = seed_and_event(continuation=True)
    # Intermediate bars extend down; next completed metric at index 25 confirms persistent OI contraction.
    for i in range(21, 25):
        step = engine.observe(snap(i, 97.0 - 0.2 * (i - 21), open_=97.2, flow=-0.3, location=0.1))
        assert step.signal is None
    continuation = engine.observe(snap(25, 95.8, open_=96.4, flow=-0.4, location=0.1))
    assert continuation.signal is not None
    assert continuation.signal.family == "OIDB_C"
    assert continuation.signal.direction == "SHORT"

    params = base_params()
    params["oidb_use_open_interest"] = False
    reference_metrics = {5 * MINUTE: metric(5 * MINUTE, 100.0, 0.5)}
    reference = OpenInterestDeleveragingBifurcationEngine(params, metrics=reference_metrics)
    started = False
    for i in range(1, 6):
        step = reference.observe(snap(i, 100.0 - i * 0.3, open_=100.0 - (i - 1) * 0.3, flow=-0.4, location=0.1))
        started = started or any(t.reason_code == "PRICE_FLOW_SHOCK_WITHOUT_OPEN_INTEREST_ABLATION" for t in step.transitions)
    assert started
    print("OIDB state-machine contracts passed")


if __name__ == "__main__":
    main()
