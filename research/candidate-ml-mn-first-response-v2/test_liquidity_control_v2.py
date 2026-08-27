#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import liquidity_control_v2 as policy


HERE = Path(__file__).resolve().parent


def synthetic_state(model: dict) -> pd.DataFrame:
    values: dict[str, object] = {}
    for column, median in zip(
        model["fill_model"]["numeric_features"],
        model["fill_model"]["numeric_medians"],
        strict=True,
    ):
        values[column] = median
    for column, fill_value in zip(
        model["fill_model"]["categorical_features"],
        model["fill_model"]["categorical_fill_values"],
        strict=True,
    ):
        values[column] = fill_value
    values.update(
        {
            "action_id": "a",
            "state_id": "s",
            "episode_id": "e",
            "research_period": "p",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "order_time_ns": 1,
            "entry_geometry": policy.GEOMETRY,
            "planned_target_net_r": 1.4,
            "source_scale_minutes": 15,
            "target_scale_minutes": 15,
            "family": "ACCEPTED_AUCTION_CONTINUATION",
            "structure_15m_trend_state": 1,
            "structure_60m_trend_state": 0,
            "structure_240m_trend_state": 0,
            "clock_hour_sin": 0.0,
            "clock_hour_cos": 1.0,
        }
    )
    return pd.DataFrame([values])


def main() -> None:
    model = json.loads(
        (HERE / "first_response_model_v2.json").read_text(encoding="utf-8")
    )
    frame = synthetic_state(model)
    engineered = policy.engineer(frame)
    fill = policy.frozen_probability(engineered, model["fill_model"])
    win = policy.frozen_probability(engineered, model["win_model"])
    assert fill.shape == win.shape == (1,)
    assert np.isfinite(fill).all() and np.isfinite(win).all()
    assert 0.0 < fill[0] < 1.0 and 0.0 < win[0] < 1.0

    changed = frame.copy()
    changed["symbol"] = "XRPUSDT"
    changed["outcome"] = "TARGET_FIRST"
    changed["net_r"] = 99.0
    changed = policy.engineer(changed)
    np.testing.assert_allclose(
        fill, policy.frozen_probability(changed, model["fill_model"])
    )
    np.testing.assert_allclose(
        win, policy.frozen_probability(changed, model["win_model"])
    )

    later = frame.copy()
    later["action_id"] = "later"
    later["state_id"] = "later-state"
    later["order_time_ns"] = 2
    states = policy.first_episode_state(pd.concat([later, frame], ignore_index=True))
    assert len(states) == 1
    assert states.iloc[0]["action_id"] == "a"


if __name__ == "__main__":
    main()
