#!/usr/bin/env python3
"""Correct one-account router for bounded candidate-1a V3 actions.

The earlier helper used ``groupby.first()``, which can assemble one synthetic row from
non-null fields belonging to different actions.  Episode arbitration must select an
actual row.  This overlay sorts causally, chooses the first completed response of each
semantic episode, resolves exact-time alternatives by predeclared economics, and then
reuses the inherited single-position 3%-NAV accounting.
"""
from __future__ import annotations

import pandas as pd

import control_transfer_policy as policy


def independent_episode_decisions(actions: pd.DataFrame) -> pd.DataFrame:
    if actions.empty:
        return actions.copy()
    ordered = actions.sort_values(
        [
            "period",
            "episode_id",
            "order_time_ns",
            "planned_target_net_r",
            "actual_fill_gross_rr",
            "risk_bps",
            "symbol",
        ],
        ascending=[True, True, True, False, False, True, True],
        kind="stable",
    )
    return (
        ordered.drop_duplicates(["period", "episode_id"], keep="first")
        .sort_values(
            ["order_time_ns", "planned_target_net_r", "actual_fill_gross_rr"],
            ascending=[True, False, False],
            kind="stable",
        )
        .reset_index(drop=True)
    )


policy.independent_episode_decisions = independent_episode_decisions

if __name__ == "__main__":
    policy.main()
