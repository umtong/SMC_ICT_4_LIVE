#!/usr/bin/env python3
"""Safe first-event sentinel repair for the frozen v9 Tardis study.

The v2 study reached causal event de-clustering but subtracted pandas' minimum
representable timestamp from the first real event, overflowing Timedelta.  This
launcher changes only the empty-state representation from a synthetic ancient
timestamp to ``None``.  Data, thresholds, regimes, costs, outcomes and promotion
rules remain unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import v9_tardis_liquidation_study as base
# Importing v2 installs canonical UTC-nanosecond readers on ``base``.
import v9_tardis_liquidation_study_v2 as timestamp_compat  # noqa: F401


def safe_within_symbol_decluster(panel: pd.DataFrame) -> pd.DataFrame:
    kept: list[int] = []
    for _, group in panel.groupby("symbol", sort=False):
        positions = group.index[group["event_candidate"]].tolist()
        last: dict[int, pd.Timestamp | None] = {-1: None, 1: None}
        for position in positions:
            row = panel.loc[position]
            direction = int(row["event_direction"])
            moment = pd.Timestamp(row["minute"])
            previous = last[direction]
            if (
                previous is not None
                and moment - previous
                < pd.Timedelta(minutes=base.WITHIN_SYMBOL_DECLUSTER_MINUTES)
            ):
                continue
            kept.append(position)
            last[direction] = moment
    return panel.loc[sorted(kept)].copy() if kept else pd.DataFrame()


base._within_symbol_decluster = safe_within_symbol_decluster


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = base.run(args.cache.resolve(), args.output.resolve())
    result["timestamp_compatibility"] = {
        "canonical_dtype": "datetime64[ns, UTC]",
        "economic_logic_changed": False,
    }
    result["first_event_sentinel"] = {
        "representation": "None until first event per direction",
        "economic_logic_changed": False,
    }
    base.write_json(args.output / "study.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
