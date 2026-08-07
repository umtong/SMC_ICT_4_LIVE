#!/usr/bin/env python3
"""Pure causal-contract tests for AFIB; no Nautilus import is required."""

from __future__ import annotations

from adaptive_flow_impact_bifurcation_engine import AdaptiveFlowImpactBifurcationEngine
from agg_trade_profile_data import AggMinuteStat
from lrb_types import BarObservation, PrimitiveSnapshot


def _snapshot(
    index: int,
    ts_ns: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    flow_ratio: float,
    lower_fast: float = 98.0,
    upper_fast: float = 102.0,
) -> PrimitiveSnapshot:
    volume = 100.0
    obs = BarObservation(
        ts_ns=ts_ns,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=volume * (flow_ratio + 1.0) / 2.0,
        trades=100,
    )
    width = high - low
    return PrimitiveSnapshot(
        index=index,
        observation=obs,
        ready=True,
        atr=1.0,
        rel_volume=1.0,
        flow_ratio=flow_ratio,
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=(high - max(open_, close)) / width,
        lower_wick_fraction=(min(open_, close) - low) / width,
        close_location=(close - low) / width,
        upper_fast=upper_fast,
        lower_fast=lower_fast,
        upper_slow=103.0,
        lower_slow=97.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


def _minute(ts_ns: int, flow: float, *, volume: float = 100.0, trades: int = 100) -> AggMinuteStat:
    return AggMinuteStat(
        end_ts_ns=ts_ns,
        total_volume=volume,
        signed_aggressive_volume=volume * flow,
        trades=trades,
        high=101.0,
        low=99.0,
        close=100.0,
    )


def _params() -> dict[str, object]:
    return {
        "afib_flow_history": 120,
        "afib_activity_history": 120,
        "afib_minimum_history": 60,
        "afib_flow_scale_floor": 0.005,
        "afib_flow_z_threshold": 2.0,
        "afib_raw_flow_ratio_threshold": 0.08,
        "afib_min_volume_ratio": 1.10,
        "afib_min_trade_ratio": 0.95,
        "afib_min_range_atr": 0.35,
        "afib_continuation_impact_atr": 0.18,
        "afib_continuation_body_atr": 0.18,
        "afib_continuation_close_location": 0.68,
        "afib_absorption_impact_atr": 0.08,
        "afib_absorption_wick_fraction": 0.25,
        "afib_absorption_close_location_ceiling": 0.58,
        "afib_confirmation_bars": 3,
        "afib_confirmation_flow_z": 0.35,
        "afib_confirmation_body_atr": 0.10,
        "afib_confirmation_close_location": 0.58,
        "afib_midpoint_tolerance_atr": 0.03,
        "afib_stop_buffer_atr": 0.05,
        "afib_projection_fraction": 1.0,
        "afib_cooldown_bars": 2,
        "afib_enable_continuation": True,
        "afib_enable_reversal": True,
        "afib_use_robust_surprise": True,
        "minimum_structural_rr": 0.80,
    }


def _baseline(engine: AdaptiveFlowImpactBifurcationEngine) -> int:
    index = 0
    for index in range(70):
        ts_ns = (index + 1) * 60_000_000_000
        flow = 0.01 if index % 2 == 0 else -0.01
        step = engine.observe(
            _snapshot(
                index,
                ts_ns,
                open_=100.0,
                high=100.2,
                low=99.8,
                close=100.0,
                flow_ratio=flow,
            ),
            allow_new=True,
        )
        assert step.signal is None
    return index + 1


def test_efficient_flow_requires_separate_follow_through() -> None:
    stats = {
        (index + 1) * 60_000_000_000: _minute(
            (index + 1) * 60_000_000_000,
            0.01 if index % 2 == 0 else -0.01,
        )
        for index in range(70)
    }
    shock_ts = 71 * 60_000_000_000
    confirm_ts = shock_ts + 60_000_000_000
    stats[shock_ts] = _minute(shock_ts, 0.30, volume=220.0, trades=180)
    stats[confirm_ts] = _minute(confirm_ts, 0.10, volume=130.0, trades=120)
    engine = AdaptiveFlowImpactBifurcationEngine(_params(), minute_stats=stats)
    index = _baseline(engine)

    shock = engine.observe(
        _snapshot(
            index,
            shock_ts,
            open_=100.0,
            high=101.0,
            low=99.8,
            close=100.9,
            flow_ratio=0.30,
        ),
        allow_new=True,
    )
    assert shock.signal is None
    assert shock.transitions[-1].next_state == "FLOW_SHOCK_CLASSIFIED"
    assert shock.transitions[-1].details["branch"] == "CONTINUATION"

    confirm_ts = shock_ts + 60_000_000_000
    confirmed = engine.observe(
        _snapshot(
            index + 1,
            confirm_ts,
            open_=100.9,
            high=101.5,
            low=100.8,
            close=101.4,
            flow_ratio=0.10,
            upper_fast=102.5,
        ),
        allow_new=True,
    )
    assert confirmed.signal is not None
    assert confirmed.signal.direction == "LONG"
    assert confirmed.signal.family == "AFIB_CONTINUATION"
    assert confirmed.transitions[-1].next_state == "ENTRY_ARMED"


def test_absorbed_buy_flow_can_reverse_only_after_opposite_response() -> None:
    stats = {
        (index + 1) * 60_000_000_000: _minute(
            (index + 1) * 60_000_000_000,
            0.01 if index % 2 == 0 else -0.01,
        )
        for index in range(70)
    }
    shock_ts = 71 * 60_000_000_000
    confirm_ts = shock_ts + 60_000_000_000
    stats[shock_ts] = _minute(shock_ts, 0.30, volume=240.0, trades=190)
    stats[confirm_ts] = _minute(confirm_ts, -0.10, volume=140.0, trades=130)
    engine = AdaptiveFlowImpactBifurcationEngine(_params(), minute_stats=stats)
    index = _baseline(engine)

    shock = engine.observe(
        _snapshot(
            index,
            shock_ts,
            open_=100.0,
            high=101.0,
            low=99.8,
            close=100.05,
            flow_ratio=0.30,
            lower_fast=98.5,
        ),
        allow_new=True,
    )
    assert shock.signal is None
    assert shock.transitions[-1].details["branch"] == "REVERSAL"

    confirm_ts = shock_ts + 60_000_000_000
    confirmed = engine.observe(
        _snapshot(
            index + 1,
            confirm_ts,
            open_=100.4,
            high=100.45,
            low=99.85,
            close=99.9,
            flow_ratio=-0.10,
            lower_fast=98.5,
        ),
        allow_new=True,
    )
    assert confirmed.signal is not None
    assert confirmed.signal.direction == "SHORT"
    assert confirmed.signal.family == "AFIB_REVERSAL"
    assert confirmed.signal.target_price == 98.5
    assert confirmed.signal.stop_price > 101.0


def test_current_shock_is_not_in_its_own_baseline() -> None:
    stats = {
        (index + 1) * 60_000_000_000: _minute(
            (index + 1) * 60_000_000_000,
            0.01 if index % 2 == 0 else -0.01,
        )
        for index in range(70)
    }
    params = _params()
    shock_ts = 71 * 60_000_000_000
    stats[shock_ts] = _minute(shock_ts, 0.30, volume=220.0, trades=180)
    engine = AdaptiveFlowImpactBifurcationEngine(params, minute_stats=stats)
    index = _baseline(engine)
    before = tuple(engine._flow_history)  # deliberate white-box causal contract
    engine.observe(
        _snapshot(
            index,
            shock_ts,
            open_=100.0,
            high=101.0,
            low=99.8,
            close=100.9,
            flow_ratio=0.30,
        ),
        allow_new=True,
    )
    assert tuple(engine._flow_history[:-1]) == before
    assert engine._flow_history[-1] == 0.30


if __name__ == "__main__":
    test_efficient_flow_requires_separate_follow_through()
    test_absorbed_buy_flow_can_reverse_only_after_opposite_response()
    test_current_shock_is_not_in_its_own_baseline()
    print("adaptive flow-impact bifurcation causal tests passed")
