#!/usr/bin/env python3
from __future__ import annotations

import candidate_4t_policy_v4 as policy


def main() -> None:
    policy.exact_feature_contract(
        ["swing_strength", "swing_distance", "auction_progress_r"],
        "execution",
    )
    for forbidden in ("win", "label_same_episode_continuation", "future_return"):
        try:
            policy.exact_feature_contract([forbidden], "continuation")
        except AssertionError:
            pass
        else:
            raise AssertionError(f"did not reject {forbidden}")
    for geometry in ("gross_rr", "entry_geometry=FVG", "route_utilization"):
        try:
            policy.exact_feature_contract([geometry], "ownership")
        except AssertionError:
            pass
        else:
            raise AssertionError(f"ownership accepted {geometry}")
    print("candidate_4t v4 feature-contract self-check: OK")


if __name__ == "__main__":
    main()
