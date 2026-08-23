#!/usr/bin/env python3
from __future__ import annotations

import numpy as np
import pandas as pd

import candidate_4t_policy_v7 as policy


def row(
    episode: str,
    action: str,
    order_time: float,
    *,
    fill_time: float = np.nan,
    resolution_time: float = np.nan,
    terminal: float = 20.0,
    filled: bool = False,
    resolved: bool = False,
    win: bool = False,
    net_r: float = np.nan,
    value: float = 0.01,
) -> dict:
    return {
        "period": "p",
        "symbol": "BTCUSDT",
        "episode_id": episode,
        "state_id": action,
        "action_id": action,
        "family": "FAILED_AUCTION",
        "side": "LONG",
        "auction_phase": "FIRST_RETEST_FORMING",
        "order_time_ns": order_time,
        "fill_time_ns": fill_time,
        "resolution_time_ns": resolution_time,
        "terminal_ns": terminal,
        "filled": filled,
        "resolved": resolved,
        "win": win,
        "net_r": net_r,
        "gross_rr": 1.5,
        "expected_enter_log": value,
        "expected_enter_log_per_hour": value,
        "expected_wait_log": 0.0,
        "stopping_advantage": value,
        "p_ownership": 0.8,
    }


def main() -> None:
    # An unresolved filled position must block every later candidate even after the
    # pending-order terminal timestamp has passed.
    unresolved = pd.DataFrame([
        row("E1", "A1", 1.0, fill_time=2.0, terminal=5.0, filled=True),
        row(
            "E2", "A2", 6.0, fill_time=6.5, resolution_time=8.0,
            terminal=8.0, filled=True, resolved=True, win=True, net_r=1.2,
            value=0.05,
        ),
    ])
    orders, trades, replacements, summary = policy.immutable_route(unresolved)
    assert len(orders) == 1
    assert str(orders.iloc[0].episode_id) == "E1"
    assert len(trades) == 0
    assert summary["open_filled_positions_at_end"] == 1
    assert summary["blocked_candidates_while_filled"] == 1

    # A resolved fill releases the slot at resolution, not at an arbitrary order
    # terminal. The next independent episode can then be selected.
    resolved = pd.DataFrame([
        row(
            "E1", "A1", 1.0, fill_time=2.0, resolution_time=4.0,
            terminal=10.0, filled=True, resolved=True, win=False, net_r=-1.0,
        ),
        row(
            "E2", "A2", 6.0, fill_time=6.5, resolution_time=8.0,
            terminal=8.0, filled=True, resolved=True, win=True, net_r=1.2,
            value=0.05,
        ),
    ])
    orders, trades, _, summary = policy.immutable_route(resolved)
    assert len(orders) == 2
    assert len(trades) == 2
    assert summary["open_filled_positions_at_end"] == 0

    # Replacement remains legal only while the first order is still unfilled.
    pending = pd.DataFrame([
        row("E1", "A1", 1.0, terminal=10.0, value=0.01),
        row("E2", "A2", 3.0, terminal=9.0, value=0.03),
    ])
    orders, _, replacements, _ = policy.immutable_route(pending)
    assert len(replacements) == 1
    assert str(orders.iloc[0].episode_id) == "E2"
    print("candidate_4t v7 self-check: OK")


if __name__ == "__main__":
    main()
