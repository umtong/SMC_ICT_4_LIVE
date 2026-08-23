#!/usr/bin/env python3
from __future__ import annotations

import pandas as pd

import candidate_4t_policy_v6 as policy


def frame(last_progress: float = 0.9) -> pd.DataFrame:
    rows = []
    for state, time_ns, progress in (("S1", 1.0, 0.1), ("S2", 2.0, 0.4), ("S3", 3.0, last_progress)):
        for action in ("MARKET", "FVG_LIMIT"):
            rows.append({
                "period": "p", "episode_id": "E1", "state_id": state,
                "order_time_ns": time_ns, "action_id": f"{state}-{action}",
                "family": "ACCEPTED_AUCTION", "side": "LONG",
                "auction_phase": "EXPANSION", "auction_progress_r": progress,
                "auction_retrace_fraction": 0.1 * time_ns,
                "entry_geometry": action, "filled": True, "resolved": True,
                "win": True, "net_r": 1.1,
            })
    return pd.DataFrame(rows)


def main() -> None:
    first = policy.add_causal_trajectory_features(frame(0.9))
    changed_future = policy.add_causal_trajectory_features(frame(-4.0))
    feature = "trajectory_delta__auction_progress_r"
    assert feature in first
    # Changing S3 cannot alter S1 or S2.
    left = first[first.state_id.isin(["S1", "S2"])][["state_id", feature]].drop_duplicates().reset_index(drop=True)
    right = changed_future[changed_future.state_id.isin(["S1", "S2"])][["state_id", feature]].drop_duplicates().reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)
    # Two entry actions at S2 share one state transition rather than counting twice.
    s2 = first[first.state_id.eq("S2")]
    assert s2.trajectory_update_count.nunique() == 1
    assert float(s2.trajectory_update_count.iloc[0]) == 2.0
    sources = policy.choose_trajectory_sources(frame())
    assert "entry_geometry" not in sources
    print("candidate_4t v6 self-check: OK")


if __name__ == "__main__":
    main()
