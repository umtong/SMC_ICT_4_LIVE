#!/usr/bin/env python3
"""Fast dependency-light checks for model causality, serialization and EV routing."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

import numpy as np

from ml_router import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    economic_geometry,
    train_router,
)


def _record(index: int, rng: np.random.Generator) -> dict[str, object]:
    signal = rng.normal()
    gross_rr = 1.0 + 0.6 * rng.random()
    target_net_r = gross_rr - 0.08
    stop_net_r = -1.08
    break_even = -stop_net_r / (target_net_r - stop_net_r)
    record: dict[str, object] = {name: 0.0 for name in NUMERIC_FEATURES}
    record.update({name: "BASE" for name in CATEGORICAL_FEATURES})
    record.update(
        {
            "gross_rr": gross_rr,
            "target_net_r": target_net_r,
            "stop_net_r": stop_net_r,
            "post_cost_reward_risk": target_net_r / abs(stop_net_r),
            "post_cost_break_even_target_probability": break_even,
            "zero_drift_target_first_prior": 1.0 / (1.0 + gross_rr),
            "required_target_probability_premium": break_even - 1.0 / (1.0 + gross_rr),
            "trace_flow_delta_share": signal,
            "trace_flow_impact_per_activity": signal + 0.1 * rng.normal(),
            "family": "FLOW" if index % 2 else "STRUCTURE",
            "side": "LONG" if index % 3 else "SHORT",
        },
    )
    return record


def main() -> None:
    rng = np.random.default_rng(37)
    records = [_record(index, rng) for index in range(240)]
    logits = np.asarray(
        [2.2 * float(record["trace_flow_delta_share"]) - 0.2 for record in records],
    )
    labels = rng.binomial(1, 1.0 / (1.0 + np.exp(-logits)))
    timestamps = np.arange(240, dtype=np.int64) * 60_000_000_000
    model = train_router(
        records,
        labels,
        timestamps,
        sample_weights=np.ones(240),
        min_category_count=2,
    )
    high = dict(records[-1], trace_flow_delta_share=2.0, trace_flow_impact_per_activity=2.0)
    low = dict(records[-1], trace_flow_delta_share=-2.0, trace_flow_impact_per_activity=-2.0)
    assert model.predict_probability(high) > model.predict_probability(low)
    assert model.decision(high).expected_net_r > model.decision(low).expected_net_r

    unknown = dict(high, family="NEVER_SEEN_FAMILY")
    probability = model.predict_probability(unknown)
    assert 0.0 < probability < 1.0

    geometry = economic_geometry(
        side="LONG",
        entry=100.0,
        stop=99.0,
        target=101.5,
        tick_size=0.01,
        entry_slippage_ticks=2,
        target_slippage_ticks=1,
        stop_slippage_ticks=2,
        entry_fee_rate=0.00075,
        exit_fee_rate=0.00075,
    )
    assert geometry["target_net_r"] > 1.0
    assert geometry["stop_net_r"] < -1.0
    assert 0.0 < geometry["post_cost_break_even_target_probability"] < 1.0

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "model.json"
        model.save(path)
        loaded = type(model).load(path)
        assert abs(loaded.predict_probability(high) - model.predict_probability(high)) < 1e-12
        payload = json.loads(path.read_text(encoding="utf-8"))
        forbidden = ("counterfactual_outcome", "mfe", "mae", "resolution")
        schema = json.dumps(
            payload["numeric_features"] + payload["categorical_features"],
        ).lower()
        assert not any(token in schema for token in forbidden)

    print(
        json.dumps(
            {
                "status": "ok",
                "dimension": model.dimension,
                "high_probability": model.predict_probability(high),
                "low_probability": model.predict_probability(low),
                "selected_ridge": model.training_metadata["selected_ridge"],
            },
            indent=2,
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()
