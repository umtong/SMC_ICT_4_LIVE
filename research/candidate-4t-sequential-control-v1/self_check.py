#!/usr/bin/env python3
from __future__ import annotations

import numpy as np
import pandas as pd

import candidate_4t_policy as policy


def row(**updates):
    base = {
        "period": "fresh-test", "symbol": "BTCUSDT", "episode_id": "E1",
        "state_id": "S1", "action_id": "A1", "family": "FAILED_AUCTION",
        "side": "LONG", "auction_phase": "FIRST_RETEST_FORMING",
        "order_time_ns": 1.0, "terminal_ns": 10.0, "fill_time_ns": np.nan,
        "filled": False, "resolved": False, "win": False, "net_r": np.nan,
        "gross_rr": 1.5, "planned_target_net_r": 1.35,
        "p_ownership": 0.75, "p_fill": 0.7, "p_resolve": 0.9,
        "expected_enter_log": 0.01, "expected_enter_log_per_hour": 0.01,
        "expected_wait_log": 0.002, "stopping_advantage": 0.008,
    }
    base.update(updates)
    return base


def main():
    # An independent better opportunity may replace a pending order.
    frame = pd.DataFrame([
        row(),
        row(episode_id="E2", state_id="S2", action_id="A2", order_time_ns=2.0,
            terminal_ns=7.0, expected_enter_log_per_hour=0.02,
            stopping_advantage=0.012, filled=True, resolved=True, win=True,
            net_r=1.2, fill_time_ns=3.0),
        # This signal arrives after E2 is filled and must not displace the position.
        row(episode_id="E3", state_id="S3", action_id="A3", order_time_ns=4.0,
            terminal_ns=6.0, expected_enter_log_per_hour=0.05,
            stopping_advantage=0.03, filled=True, resolved=True, win=True,
            net_r=2.0, fill_time_ns=4.5),
    ])
    orders, trades, replacements, summary = policy.route(frame)
    assert len(replacements) == 1
    assert len(trades) == 1
    assert str(trades.iloc[0].episode_id) == "E2"
    assert summary["ending_nav_multiplier"] > 1.0

    state = pd.DataFrame([
        {"period": "p", "episode_id": "e", "state_id": "1", "order_time_ns": 1,
         "raw": 0.65, "prior": 0.5, "phase": "EARLY_RESPONSE", "progress": 0.1, "failure": 0.0},
        {"period": "p", "episode_id": "e", "state_id": "2", "order_time_ns": 2,
         "raw": 0.75, "prior": 0.5, "phase": "ACCEPTED_EXPANSION", "progress": 0.4, "failure": 0.0},
        {"period": "p", "episode_id": "e", "state_id": "3", "order_time_ns": 3,
         "raw": 0.2, "prior": 0.5, "phase": "FAILED_REENTRY", "progress": -0.2, "failure": 1.2},
    ])
    posterior = policy.apply_ownership_filter(state, 0.6, 0.9)
    assert posterior.iloc[1] > posterior.iloc[0]
    assert posterior.iloc[2] < 0.5
    print("candidate_4t self-check: OK")


if __name__ == "__main__":
    main()
