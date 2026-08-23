#!/usr/bin/env python3
from __future__ import annotations

import pandas as pd

import candidate_4t_policy_v5 as policy


def main() -> None:
    # Immediate-state ownership must dominate a conflicting limit-entry label.
    frame = pd.DataFrame([
        {
            "period": "p", "episode_id": "e", "state_id": "s",
            "order_time_ns": 1.0, "action_id": "market", "entry_geometry": "MARKET",
            "filled": True, "resolved": True, "net_r": -1.0, "win": False,
        },
        {
            "period": "p", "episode_id": "e", "state_id": "s",
            "order_time_ns": 1.0, "action_id": "limit", "entry_geometry": "FVG_LIMIT",
            "filled": True, "resolved": True, "net_r": 1.2, "win": True,
        },
    ])
    state = policy.market_state_ownership_training(frame)
    assert float(state[policy.core.LABEL_OWNERSHIP].iloc[0]) == 0.0

    # An accepted hypothesis must not donate its accumulated belief to a later
    # failed-auction reversal in the opposite direction.
    states = pd.DataFrame([
        {
            "period": "p", "episode_id": "e", "state_id": "1", "order_time_ns": 1,
            "family": "ACCEPTED_AUCTION", "side": "LONG", "phase": "BREAK_HOLD",
            "raw": 0.82, "prior": 0.5, "progress": 0.3, "failure": 0.0,
        },
        {
            "period": "p", "episode_id": "e", "state_id": "2", "order_time_ns": 2,
            "family": "ACCEPTED_AUCTION", "side": "LONG", "phase": "EXPANSION",
            "raw": 0.84, "prior": 0.5, "progress": 0.5, "failure": 0.0,
        },
        {
            "period": "p", "episode_id": "e", "state_id": "3", "order_time_ns": 3,
            "family": "FAILED_AUCTION", "side": "SHORT", "phase": "RECLAIM_CONTROL_TRANSFER",
            "raw": 0.55, "prior": 0.5, "progress": 0.05, "failure": 0.0,
        },
    ])
    posterior = policy.apply_hypothesis_filter(states, 0.7, 1.0)
    assert float(posterior.iloc[1]) > 0.8
    assert float(posterior.iloc[2]) < 0.62
    print("candidate_4t v5 self-check: OK")


if __name__ == "__main__":
    main()
