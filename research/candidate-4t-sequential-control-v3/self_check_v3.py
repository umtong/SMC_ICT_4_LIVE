#!/usr/bin/env python3
from __future__ import annotations

import numpy as np
import pandas as pd

import candidate_4t_policy_v3 as policy


def make_action(
    state_id: str,
    action_id: str,
    *,
    episode_id: str = "E1",
    order_time_ns: float = 1.0,
    terminal_ns: float = 10.0,
    win: bool = True,
) -> dict:
    return {
        "period": "p",
        "symbol": "BTCUSDT",
        "episode_id": episode_id,
        "state_id": state_id,
        "action_id": action_id,
        "order_time_ns": order_time_ns,
        "terminal_ns": terminal_ns,
        "family": "FAILED_AUCTION",
        "side": "LONG",
        "auction_phase": "FIRST_RETEST_FORMING",
        "filled": True,
        "resolved": True,
        "win": win,
        "net_r": 1.2 if win else -1.0,
        "expected_enter_log": 0.02,
        "predicted_terminal_minutes": 60.0,
        "p_fill": 0.7,
    }


def main() -> None:
    actions = pd.DataFrame([
        make_action("S1", "A1", win=True),
        make_action("S1", "A2", win=False),
        make_action("S2", "A3", order_time_ns=2.0, win=True),
    ])
    ownership = policy.ownership_training_states(actions)
    assert len(ownership) == 2
    target = ownership.loc[
        ownership.state_id.eq("S1"), policy.LABEL_OWNERSHIP
    ].iloc[0]
    assert abs(float(target) - 0.5) < 1e-12
    assert policy.LABEL_OWNERSHIP.startswith("label_")

    states = pd.DataFrame([
        make_action("S1", "A1", order_time_ns=1.0, terminal_ns=8.0),
        make_action("S2", "A2", order_time_ns=2.0, terminal_ns=8.0),
        make_action(
            "S3", "A3", episode_id="E2", order_time_ns=3.0, terminal_ns=8.0
        ),
        make_action(
            "S4", "A4", episode_id="E3", order_time_ns=9.0, terminal_ns=12.0
        ),
    ])
    states.loc[states.state_id.eq("S2"), "expected_enter_log"] = 0.08
    states.loc[states.state_id.eq("S3"), "expected_enter_log"] = 0.03
    labeled = policy.attach_training_continuation_labels(states)
    first = labeled.loc[labeled.state_id.eq("S1")].iloc[0]
    assert float(first[policy.LABEL_SAME_WAIT]) > float(
        first[policy.LABEL_GLOBAL_SLOT]
    ) > 0.0
    assert float(
        labeled.loc[labeled.state_id.eq("S4"), policy.LABEL_GLOBAL_SLOT].iloc[0]
    ) == 0.0

    policy._assert_feature_contract(
        ["auction_progress_r", "family=FAILED_AUCTION"], "ownership"
    )
    try:
        policy._assert_feature_contract(["label_future_value"], "continuation")
    except AssertionError:
        pass
    else:
        raise AssertionError("future label was not rejected")
    try:
        policy._assert_feature_contract(["gross_rr"], "ownership")
    except AssertionError:
        pass
    else:
        raise AssertionError("entry geometry was not rejected from ownership")

    # Fresh prediction inputs intentionally contain no training-only continuation labels.
    fresh = policy.canonical_states(states)
    assert policy.LABEL_SAME_WAIT not in fresh.columns
    assert policy.LABEL_GLOBAL_SLOT not in fresh.columns
    print("candidate_4t v3 self-check: OK")


if __name__ == "__main__":
    main()
