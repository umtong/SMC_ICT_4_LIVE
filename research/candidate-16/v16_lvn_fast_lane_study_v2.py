#!/usr/bin/env python3
"""Early-contact finality repair for the frozen v16 LVN study.

The base detector begins at minute 20 because its volume-release predicate uses
20 completed prior minutes.  A fresh lane, however, is consumed by its first
physical next-day contact even when that contact occurs before the volume
baseline is ready.  This adapter fail-closes those early contacts instead of
allowing a favorable later contact.  Profile construction, thresholds, state,
transition, entry, stop, target, costs, outcomes and promotion rules do not
change.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import v16_lvn_fast_lane_study as base


_ORIGINAL_DETECT = base.detect_lane_candidate


def strict_detect_lane_candidate(
    *,
    symbol: str,
    frame: pd.DataFrame,
    lane: base.FastLane,
) -> base.LaneCandidate | None:
    if frame.empty:
        return None
    close = pd.to_numeric(frame["perp_close"], errors="coerce")
    previous = close.shift(1)
    for position in range(1, min(base.ENTRY_VOLUME_LOOKBACK, len(frame))):
        row = frame.iloc[position]
        prior_close = float(previous.iloc[position])
        long_contact = (
            prior_close < lane.lower_entry_edge
            and float(row["perp_high"]) >= lane.lower_entry_edge
        )
        short_contact = (
            prior_close > lane.upper_entry_edge
            and float(row["perp_low"]) <= lane.upper_entry_edge
        )
        if long_contact or short_contact:
            return None
    return _ORIGINAL_DETECT(symbol=symbol, frame=frame, lane=lane)


base.detect_lane_candidate = strict_detect_lane_candidate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = base.run(args.cache.resolve(), args.output.resolve())
    result["early_contact_finality"] = {
        "minutes_before_volume_baseline": base.ENTRY_VOLUME_LOOKBACK,
        "policy": "any physical contact consumes lane and produces no trade",
        "economic_logic_changed": False,
    }
    base.write_json(args.output / "study.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
