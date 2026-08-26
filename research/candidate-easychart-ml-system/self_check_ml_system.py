#!/usr/bin/env python3
"""Dependency-light checks for the robust ML system core."""
from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from causal_state import CausalMarketState, STATE_FEATURES
from robust_router import (
    NUMERIC_FEATURES,
    RobustPlanRouter,
    train_robust_router,
)


def _bar(minute: int, symbol_index: int, future_shift: float = 0.0) -> SimpleNamespace:
    base = 100.0 + 20.0 * symbol_index
    drift = 0.00012 * minute + future_shift
    open_price = base * math.exp(drift)
    impulse = 0.0007 * math.sin(minute / 7.0 + symbol_index) + 0.00015 * (symbol_index - 1.5)
    close = open_price * math.exp(impulse)
    high = max(open_price, close) * 1.0003
    low = min(open_price, close) * 0.9997
    quote = 1_000_000.0 * (1.0 + 0.2 * symbol_index + 0.1 * abs(math.sin(minute / 11.0)))
    taker_share = min(0.85, max(0.15, 0.5 + 0.18 * math.sin(minute / 9.0 + symbol_index)))
    return SimpleNamespace(
        ts_close_ns=(minute + 1) * 60_000_000_000,
        open=open_price,
        high=high,
        low=low,
        close=close,
        quote_volume=quote,
        trade_count=500 + 30 * symbol_index,
        taker_buy_quote_volume=quote * taker_share,
    )


def _state_after(minutes: int, future_shift_after: int | None = None) -> dict[str, float]:
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    state = CausalMarketState(symbols)
    captured: dict[str, float] = {}
    for minute in range(minutes):
        shift = 0.0 if future_shift_after is None or minute <= future_shift_after else 0.5
        for index, symbol in enumerate(symbols):
            state.observe(symbol, _bar(minute, index, shift))
        state.finalize()
        if minute == 299:
            captured = dict(state.snapshot("BTCUSDT"))
    return captured


def main() -> None:
    first = _state_after(340, future_shift_after=None)
    changed_future = _state_after(340, future_shift_after=299)
    assert set(first) == set(STATE_FEATURES)
    for name in STATE_FEATURES:
        left = first[name]
        right = changed_future[name]
        if math.isnan(left) and math.isnan(right):
            continue
        assert abs(left - right) < 1e-12, (name, left, right)

    rng = np.random.default_rng(19)
    environments = ("2024Q1", "2024Q2", "2024Q3", "2025Q1")
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    records = []
    labels = []
    timestamps = []
    environment_values = []
    symbol_values = []
    weights = []
    for index in range(720):
        environment = environments[index % len(environments)]
        symbol = symbols[(index // len(environments)) % len(symbols)]
        persistence = float(rng.normal())
        aligned_flow = float(rng.normal())
        location_quality = float(rng.normal())
        logit = 1.35 * persistence + 0.95 * aligned_flow + 0.75 * persistence * location_quality
        probability = 1.0 / (1.0 + math.exp(-logit))
        label = int(rng.random() < probability)
        record = {name: 0.0 for name in NUMERIC_FEATURES}
        record.update(
            {
                "gross_rr": 1.5,
                "target_net_r": 1.35,
                "stop_net_r": -1.08,
                "post_cost_break_even_target_probability": 1.08 / (1.35 + 1.08),
                "mls_return_z_15m": persistence,
                "mls_delta_share_15m": aligned_flow,
                "entry_location_in_overlap": location_quality,
                "mechanism_owner": "FLOW",
                "family": "FLOW|SYNTHETIC_REJECTION",
                "scenario_path": "REJECTION",
                "scale_name": "MICRO",
                "higher_zone_kind": "STRUCTURE",
                "lower_zone_kind": "STRUCTURE",
                "trigger_zone_kind": "FLOW_ABSORPTION",
                "target_zone_kind": "OPPOSING_LIQUIDITY",
            },
        )
        records.append(record)
        labels.append(label)
        timestamps.append(index + 1)
        environment_values.append(environment)
        symbol_values.append(symbol)
        weights.append(1.0)

    router = train_robust_router(
        records,
        labels,
        timestamps,
        environment_values,
        symbol_values,
        weights,
        min_category_count=2,
        trees=30,
        depth=2,
        feature_subsample=80,
        seed=23,
    )
    assert "symbol" not in router.encoder.numeric_features
    assert "symbol" not in router.encoder.categorical_features
    assert "environment" not in router.encoder.numeric_features
    assert "environment" not in router.encoder.categorical_features

    favorable = dict(records[0])
    favorable.update(
        {
            "mls_return_z_15m": 2.5,
            "mls_delta_share_15m": 1.2,
            "entry_location_in_overlap": 1.0,
        },
    )
    adverse = dict(records[0])
    adverse.update(
        {
            "mls_return_z_15m": -2.5,
            "mls_delta_share_15m": -1.2,
            "entry_location_in_overlap": -1.0,
        },
    )
    favorable_probability, _ = router.predict_probability(favorable)
    adverse_probability, _ = router.predict_probability(adverse)
    assert favorable_probability > adverse_probability + 0.03, (
        favorable_probability,
        adverse_probability,
    )
    assert router.decision(favorable).expected_log_growth > router.decision(adverse).expected_log_growth

    destination = Path("/tmp/easychart_ml_system_self_check.json")
    router.save(destination)
    loaded = RobustPlanRouter.load(destination)
    assert abs(
        loaded.predict_probability(favorable)[0]
        - favorable_probability
    ) < 1e-12
    result = {
        "status": "ok",
        "state_features": len(STATE_FEATURES),
        "model_dimension": router.dimension,
        "ensemble_members": len(router.models),
        "favorable_probability": favorable_probability,
        "adverse_probability": adverse_probability,
        "favorable_expected_log_growth": router.decision(favorable).expected_log_growth,
        "adverse_expected_log_growth": router.decision(adverse).expected_log_growth,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
