from __future__ import annotations

from decimal import Decimal
import math

import numpy as np
import pandas as pd

import v104_external_liquidity_core as v104
from v53_nt_core import CostConfig


def _raw(index: pd.DatetimeIndex, price: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": price,
            "high": price + 0.10,
            "low": price - 0.10,
            "close": price,
        },
        index=index,
        dtype=float,
    )


def _costs() -> CostConfig:
    return CostConfig(
        entry_fee_rate=Decimal("0"),
        target_fee_rate=Decimal("0"),
        stop_fee_rate=Decimal("0"),
        entry_slippage_rate=Decimal("0"),
        stop_slippage_rate=Decimal("0"),
        market_impact_rate=Decimal("0"),
        funding_rate_allowance=Decimal("0"),
    )


def test_completed_higher_timeframe_bar_requires_every_minute() -> None:
    index = pd.date_range("2025-01-01 00:01", periods=15, freq="min", tz="UTC")
    raw = _raw(index)
    complete = v104._completed_bars(
        raw,
        start=index[0] - pd.Timedelta(minutes=1),
        end=index[-1],
        minutes=15,
    )
    incomplete = v104._completed_bars(
        raw.drop(index[7]),
        start=index[0] - pd.Timedelta(minutes=1),
        end=index[-1],
        minutes=15,
    )
    assert len(complete) == 1
    assert incomplete.empty


def test_previous_day_liquidity_exists_only_after_full_day_close() -> None:
    index = pd.date_range("2025-01-01 00:01", periods=2 * 1440, freq="min", tz="UTC")
    raw = _raw(index)
    raw.loc[index[:1440], "high"] = np.linspace(100.0, 105.0, 1440)
    config = v104.ExternalLiquidityConfig()
    levels = v104._period_levels(raw, config)
    first_day_high = next(
        level
        for level in levels
        if level.family == "PREVIOUS_DAY"
        and level.side == "HIGH"
        and level.metadata["period_start_utc"].startswith("2025-01-01")
    )
    expected = pd.Timestamp("2025-01-02 00:00", tz="UTC")
    assert first_day_high.eligibility_ns == int(expected.value)
    assert math.isclose(first_day_high.price, 105.0)
    assert first_day_high.confirmation_ns == first_day_high.eligibility_ns


def test_displacement_search_cannot_use_pre_acceptance_candle() -> None:
    index = pd.date_range("2025-01-01 00:01", periods=10, freq="min", tz="UTC")
    x = _raw(index)
    x = x.rename(columns={"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close"})
    x["body"] = 0.01
    x["body_threshold"] = 0.05
    x["atr"] = 1.0
    # A valid-looking displacement exists at position 3, but acceptance closes at 5.
    x.iloc[1, x.columns.get_loc("raw_high")] = 99.5
    x.iloc[3, x.columns.get_loc("raw_open")] = 100.0
    x.iloc[3, x.columns.get_loc("raw_low")] = 100.1
    x.iloc[3, x.columns.get_loc("raw_close")] = 101.0
    x.iloc[3, x.columns.get_loc("raw_high")] = 101.2
    x.iloc[3, x.columns.get_loc("body")] = 1.0
    config = v104.ExternalLiquidityConfig(
        displacement_search_minutes=3,
        minimum_displacement_body_atr=0.2,
    )
    assert v104._find_displacement(
        x=x,
        start_position=5,
        boundary=100.0,
        direction=1,
        config=config,
    ) is None


def test_displacement_search_cannot_reuse_acceptance_close() -> None:
    index = pd.date_range("2025-01-01 00:01", periods=10, freq="min", tz="UTC")
    x = _raw(index).rename(
        columns={"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close"}
    )
    x["body"] = 0.01
    x["body_threshold"] = 0.05
    x["atr"] = 1.0
    acceptance = 5
    x.iloc[acceptance - 2, x.columns.get_loc("raw_high")] = 99.5
    x.iloc[acceptance, x.columns.get_loc("raw_open")] = 100.0
    x.iloc[acceptance, x.columns.get_loc("raw_low")] = 100.1
    x.iloc[acceptance, x.columns.get_loc("raw_close")] = 101.0
    x.iloc[acceptance, x.columns.get_loc("raw_high")] = 101.2
    x.iloc[acceptance, x.columns.get_loc("body")] = 1.0
    config = v104.ExternalLiquidityConfig(
        displacement_search_minutes=3,
        minimum_displacement_body_atr=0.2,
    )
    assert v104._find_displacement(
        x=x,
        start_position=acceptance,
        boundary=100.0,
        direction=1,
        config=config,
    ) is None


def test_nearest_target_is_not_skipped_to_manufacture_rr() -> None:
    now = int(pd.Timestamp("2025-01-01", tz="UTC").value)
    levels = [
        v104.LiquidityLevel("near", "PREVIOUS_DAY", "HIGH", 107.0, now, now, now, now + 10**15),
        v104.LiquidityLevel("far", "PREVIOUS_WEEK", "HIGH", 120.0, now, now, now, now + 10**15),
    ]
    config = v104.ExternalLiquidityConfig(minimum_target_cost_after_rr=1.0)
    result = v104._select_natural_target(
        candidates=levels,
        side="BUY",
        boundary=100.0,
        entry=105.0,
        stop=100.0,
        costs=_costs(),
        config=config,
    )
    assert result is None  # 107 is <1R; 120 must not be substituted.


def test_equal_swing_ablation_changes_only_level_family(monkeypatch) -> None:
    index = pd.date_range("2025-01-01 00:01", periods=60, freq="min", tz="UTC")
    raw = _raw(index)
    atr = pd.Series(1.0, index=index)
    now = int(index[0].value)
    pd_level = v104.LiquidityLevel("pd", "PREVIOUS_DAY", "HIGH", 110, now, now, now, now + 10**15)
    mature = v104.LiquidityLevel("m", "MATURE_SWING", "LOW", 90, now, now, now, now + 10**15)
    equal = v104.LiquidityLevel("eq", "EQUAL_SWING_CLUSTER", "HIGH", 111, now, now, now, now + 10**15)
    monkeypatch.setattr(v104, "_confirmed_swings", lambda raw, config: [])
    monkeypatch.setattr(v104, "_period_levels", lambda raw, config: [pd_level])
    monkeypatch.setattr(v104, "_mature_swing_levels", lambda raw, swings, atr, config: [mature])
    monkeypatch.setattr(v104, "_equal_swing_levels", lambda raw, swings, atr, config: [equal])

    baseline = v104.build_liquidity_registry(raw, atr=atr, config=v104.ExternalLiquidityConfig())
    ablated = v104.build_liquidity_registry(
        raw,
        atr=atr,
        config=v104.ExternalLiquidityConfig(
            level_families=("PREVIOUS_DAY", "PREVIOUS_WEEK", "MATURE_SWING"),
        ),
    )
    assert {x.level_id for x in baseline} == {"pd", "m", "eq"}
    assert {x.level_id for x in ablated} == {"pd", "m"}


def test_signal_is_activated_one_completed_minute_after_decision(monkeypatch) -> None:
    index = pd.date_range("2025-01-01 00:01", periods=410, freq="min", tz="UTC")
    raw = _raw(index, 99.5)
    state = pd.DataFrame(
        {
            "close": 99.5,
            "aggressive_total_quote_1m": 100.0,
            "signed_flow_ratio_1m": 0.0,
            "spot_close": 99.5,
            "spot_signed_flow_ratio_1m": 0.0,
            "perp_spot_log_basis": 0.0,
            "turnover_threshold": 50.0,
        },
        index=index,
        dtype=float,
    )
    event = 370
    raw.iloc[event - 1] = [99.5, 99.6, 99.4, 99.5]
    raw.iloc[event] = [99.8, 100.5, 99.7, 100.2]
    raw.iloc[event + 1] = [100.2, 100.45, 100.1, 100.3]
    raw.iloc[event + 2] = [100.25, 100.55, 100.15, 100.4]
    raw.iloc[event + 3] = [100.65, 101.2, 100.60, 101.0]
    raw.iloc[event + 4] = [100.7, 100.70, 100.03, 100.60]
    raw.iloc[event + 5] = [100.6, 100.8, 100.5, 100.7]
    state.loc[index[event : event + 6], "spot_close"] = raw.loc[index[event : event + 6], "close"]
    state.loc[index[event : event + 6], "close"] = raw.loc[index[event : event + 6], "close"]

    eligibility = int(index[0].value)
    expiry = int(index[-1].value) + 10**15
    boundary = v104.LiquidityLevel(
        "boundary", "PREVIOUS_DAY", "HIGH", 100.0,
        eligibility, eligibility, eligibility, expiry,
    )
    target = v104.LiquidityLevel(
        "target", "PREVIOUS_WEEK", "HIGH", 110.0,
        eligibility, eligibility, eligibility, expiry,
    )
    monkeypatch.setattr(
        v104,
        "build_liquidity_registry",
        lambda raw, atr, config: [boundary, target],
    )
    config = v104.ExternalLiquidityConfig(
        prior_window_minutes=1440,
        prior_minimum_minutes=360,
        maximum_event_extension_atr=3.0,
        classification_minutes=3,
        minimum_outside_closes=2,
        minimum_acceptance_atr=0.0,
        minimum_spot_acceptance_ratio=0.0,
        maximum_basis_expansion_share=1.0,
        displacement_search_minutes=3,
        displacement_body_quantile=0.50,
        minimum_displacement_body_atr=0.20,
        minimum_fvg_atr=0.0,
        maximum_retest_boundary_distance_atr=0.50,
        minimum_target_cost_after_rr=0.0,
        activation_delay_minutes=1,
    )
    result = v104.build_scenario_result(
        state=state,
        raw=raw,
        evaluation_start=index[event],
        evaluation_end=index[event + 10],
        config=config,
        costs=_costs(),
    )
    assert len(result.signals) == 1, result.diagnostics
    signal = result.signals[0]
    decision_ns = int(index[event + 4].value)
    assert signal.source_max_market_time_ns == decision_ns
    assert signal.observed_time_ns == decision_ns + v104.NS_MINUTE
    assert signal.source_feature_available_time_ns == signal.observed_time_ns
    assert signal.details["decision_close_utc"] == index[event + 4].isoformat()
    assert signal.details["activation_delay_minutes"] == 1


def test_target_must_remain_active_at_order_activation() -> None:
    decision_ns = int(pd.Timestamp("2025-01-01 00:01", tz="UTC").value)
    activation_ns = decision_ns + v104.NS_MINUTE
    target = v104.LiquidityLevel(
        "expiring",
        "PREVIOUS_DAY",
        "HIGH",
        110.0,
        decision_ns,
        decision_ns,
        decision_ns,
        decision_ns,
    )
    candidates = v104._target_candidates(
        levels=[target],
        consumed=set(),
        decision_ns=decision_ns,
        activation_ns=activation_ns,
        side="BUY",
        entry=104.0,
        path_extreme=104.5,
    )
    assert candidates == []


def test_target_confirmed_only_on_activation_is_future_information() -> None:
    decision_ns = int(pd.Timestamp("2025-01-01 00:01", tz="UTC").value)
    activation_ns = decision_ns + v104.NS_MINUTE
    target = v104.LiquidityLevel(
        "future-confirmation",
        "MATURE_SWING",
        "HIGH",
        110.0,
        decision_ns,
        activation_ns,
        activation_ns,
        activation_ns + 10 * v104.NS_MINUTE,
    )
    assert v104._target_candidates(
        levels=[target],
        consumed=set(),
        decision_ns=decision_ns,
        activation_ns=activation_ns,
        side="BUY",
        entry=104.0,
        path_extreme=104.5,
    ) == []


def test_actual_activation_price_rechecks_rr_and_delivery_fraction() -> None:
    rr_degraded = v104.validate_activation(
        side="BUY",
        entry=105.5,
        boundary=100.0,
        stop=99.0,
        target=110.0,
        costs=_costs(),
        minimum_cost_after_rr=1.0,
        maximum_delivery_fraction=1.0,
        activation_high=106.0,
        activation_low=105.0,
    )
    assert not rr_degraded.accepted
    assert rr_degraded.reason == "ACTIVATION_COST_AFTER_RR_BELOW_FLOOR"

    delivery_degraded = v104.validate_activation(
        side="BUY",
        entry=105.5,
        boundary=100.0,
        stop=99.0,
        target=110.0,
        costs=_costs(),
        minimum_cost_after_rr=0.0,
        maximum_delivery_fraction=0.5,
        activation_high=106.0,
        activation_low=105.0,
    )
    assert not delivery_degraded.accepted
    assert delivery_degraded.reason == "ACTIVATION_DELIVERY_FRACTION_EXCEEDED"


def test_activation_bar_cannot_have_already_completed_or_invalidated_scenario() -> None:
    target_traversed = v104.validate_activation(
        side="BUY",
        entry=104.0,
        boundary=100.0,
        stop=99.0,
        target=110.0,
        costs=_costs(),
        minimum_cost_after_rr=1.0,
        maximum_delivery_fraction=0.5,
        activation_high=110.0,
        activation_low=103.0,
    )
    assert not target_traversed.accepted
    assert target_traversed.reason == "ACTIVATION_BAR_PRETRAVERSED_TARGET"

    stop_traversed = v104.validate_activation(
        side="SELL",
        entry=96.0,
        boundary=100.0,
        stop=101.0,
        target=90.0,
        costs=_costs(),
        minimum_cost_after_rr=1.0,
        maximum_delivery_fraction=0.5,
        activation_high=101.0,
        activation_low=95.0,
    )
    assert not stop_traversed.accepted
    assert stop_traversed.reason == "ACTIVATION_BAR_PRETRAVERSED_STOP"


def test_activation_bar_cannot_traverse_structural_invalidation_even_if_stop_survives() -> None:
    result = v104.validate_activation(
        side="BUY",
        entry=104.0,
        boundary=100.0,
        stop=98.0,
        target=110.0,
        costs=_costs(),
        minimum_cost_after_rr=1.0,
        maximum_delivery_fraction=0.5,
        activation_high=104.5,
        activation_low=99.4,
        structural_invalidation=99.5,
    )
    assert not result.accepted
    assert result.reason == "ACTIVATION_BAR_PRETRAVERSED_STRUCTURAL_INVALIDATION"


def test_activation_delay_is_exactly_one_completed_minute() -> None:
    try:
        v104.ExternalLiquidityConfig(activation_delay_minutes=2)
    except ValueError as exc:
        assert "exactly one completed minute" in str(exc)
    else:
        raise AssertionError("v104 must not silently support an unobserved multi-bar activation delay")
