#!/usr/bin/env python3
from __future__ import annotations

import pandas as pd

import liquidity_control_v3 as policy


def synthetic_rows() -> pd.DataFrame:
    base = {
        "research_period": "synthetic",
        "episode_id": "episode-1",
        "state_id": "state-1",
        "action_id": "early",
        "order_time_ns": 100,
        "entry_geometry": policy.base.GEOMETRY,
        "planned_target_net_r": 1.4,
        "event_common_return_1m_signed": -0.0010,
        "auction_acceptance_strength": 2.5,
        "symbol": "BTCUSDT",
        "outcome": "STOP_FIRST",
        "net_r": -1.0,
    }
    later = dict(base)
    later.update(state_id="state-2", action_id="later", order_time_ns=200, outcome="TARGET_FIRST", net_r=1.4)
    rejected = dict(base)
    rejected.update(
        episode_id="episode-2",
        state_id="state-3",
        action_id="not-accepted",
        order_time_ns=300,
        auction_acceptance_strength=2.19,
    )
    return pd.DataFrame([later, rejected, base])


def main() -> None:
    frame = synthetic_rows()
    selected = policy.select_plans(frame)
    assert selected["action_id"].tolist() == ["early"]
    assert selected["scenario_family"].eq("COMMON_SWEEP_ACCEPTANCE_RECLAIM").all()

    mutated = frame.copy()
    mutated["symbol"] = "XRPUSDT"
    mutated["outcome"] = "TARGET_FIRST"
    mutated["net_r"] = 99.0
    mutated["mfe_r"] = 99.0
    mutated["mae_r"] = 0.0
    assert policy.select_plans(mutated)["action_id"].tolist() == selected["action_id"].tolist()

    boundary = frame.iloc[[0]].copy()
    boundary["action_id"] = "boundary"
    boundary["episode_id"] = "episode-boundary"
    boundary["event_common_return_1m_signed"] = policy.EVENT_COMMON_SWEEP
    boundary["auction_acceptance_strength"] = policy.MIN_ACCEPTANCE_STRENGTH
    assert policy.select_plans(boundary)["action_id"].tolist() == ["boundary"]


if __name__ == "__main__":
    main()
