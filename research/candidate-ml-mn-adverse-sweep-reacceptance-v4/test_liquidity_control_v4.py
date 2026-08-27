#!/usr/bin/env python3
from __future__ import annotations

import pandas as pd

import liquidity_control_v4 as policy


def row(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "research_period": "synthetic",
        "episode_id": "episode-a",
        "state_id": "state-a",
        "action_id": "event-first",
        "order_time_ns": 100,
        "entry_geometry": policy.base.GEOMETRY,
        "planned_target_net_r": 1.4,
        "family": "FAILED_AUCTION_REVERSAL",
        "location_kind": "IFVG_OB_OVERLAP",
        "event_body_bps_signed": -20.0,
        "auction_phase": "ACCEPTED_EXPANSION",
        "sequence_block_3_activity_ratio": 0.8,
        "symbol": "BTCUSDT",
        "outcome": "STOP_FIRST",
        "net_r": -1.0,
    }
    value.update(updates)
    return value


def main() -> None:
    frame = pd.DataFrame(
        [
            row(
                episode_id="episode-b",
                state_id="state-b",
                action_id="deep",
                order_time_ns=300,
                event_body_bps_signed=-5.0,
                location_kind="IFVG",
                auction_phase="DEEP_RETEST",
                sequence_block_3_activity_ratio=1.0,
            ),
            row(state_id="state-a-later", action_id="event-later", order_time_ns=200),
            row(),
            row(
                episode_id="episode-c",
                state_id="state-c",
                action_id="below-boundary",
                order_time_ns=400,
                event_body_bps_signed=-17.999,
            ),
        ]
    )
    selected = policy.select_plans(frame)
    assert selected["action_id"].tolist() == ["event-first", "deep"]
    assert selected["scenario_family"].tolist() == [
        "ADVERSE_EVENT_OVERLAP_RECLAIM",
        "DEEP_RETEST_ACTIVITY_REACCEPTANCE",
    ]

    mutated = frame.copy()
    mutated["symbol"] = "XRPUSDT"
    mutated["outcome"] = "TARGET_FIRST"
    mutated["net_r"] = 99.0
    mutated["mfe_r"] = 99.0
    mutated["mae_r"] = 0.0
    assert policy.select_plans(mutated)["action_id"].tolist() == selected["action_id"].tolist()

    boundary = pd.DataFrame(
        [
            row(
                episode_id="episode-boundary-a",
                state_id="boundary-a",
                action_id="boundary-event",
                event_body_bps_signed=policy.EVENT_BODY_BPS_MAX,
            ),
            row(
                episode_id="episode-boundary-b",
                state_id="boundary-b",
                action_id="boundary-deep",
                event_body_bps_signed=-5.0,
                location_kind="IFVG",
                auction_phase="DEEP_RETEST",
                sequence_block_3_activity_ratio=policy.DEEP_ACTIVITY_RATIO_MIN,
            ),
        ]
    )
    assert policy.select_plans(boundary)["action_id"].tolist() == [
        "boundary-event",
        "boundary-deep",
    ]


if __name__ == "__main__":
    main()
