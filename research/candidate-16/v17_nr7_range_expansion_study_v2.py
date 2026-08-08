#!/usr/bin/env python3
"""Early-contact finality repair for the frozen v17 NR7 study.

The base detector starts at minute 20 because the volume predicate needs twenty
completed observations.  NR7's first next-day contact is nevertheless final.
This adapter fail-closes any contact with either prior-day boundary before the
volume baseline is ready, rather than allowing a favorable later direction or
entry.  Context, thresholds, transition, stop, measured target, costs, outcomes
and promotion rules remain unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import v17_nr7_range_expansion_study as base


_ORIGINAL_DETECT = base.detect_candidate


def strict_detect_candidate(
    *,
    symbol: str,
    state: base.DailyRangeState,
    next_day: pd.DataFrame,
) -> base.NR7Candidate | None:
    if not state.nr7 or next_day.empty:
        return None
    close = pd.to_numeric(next_day["perp_close"], errors="coerce")
    previous = close.shift(1)
    for position in range(1, min(base.ENTRY_VOLUME_LOOKBACK, len(next_day))):
        row = next_day.iloc[position]
        prior_close = float(previous.iloc[position])
        high_contact = prior_close <= state.high and float(row["perp_high"]) >= state.high
        low_contact = prior_close >= state.low and float(row["perp_low"]) <= state.low
        if high_contact or low_contact:
            return None
    return _ORIGINAL_DETECT(symbol=symbol, state=state, next_day=next_day)


base.detect_candidate = strict_detect_candidate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = base.run(args.cache.resolve(), args.output.resolve())
    result["early_contact_finality"] = {
        "minutes_before_volume_baseline": base.ENTRY_VOLUME_LOOKBACK,
        "policy": "any prior-day-boundary contact consumes NR7 state and produces no trade",
        "economic_logic_changed": False,
    }
    base.write_json(args.output / "study.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
