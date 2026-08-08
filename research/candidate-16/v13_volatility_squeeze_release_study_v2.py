#!/usr/bin/env python3
"""Session-diversity evidence adapter for the frozen v13 rolling squeeze study.

The base study is time-of-day agnostic.  Its shared summary helper expects a
``session`` field for robustness attribution, so this launcher assigns entry
records to four fixed UTC time blocks without changing detection, entry, stop,
target, cost, outcome or promotion thresholds.  This prevents a rolling system
from trivially failing the predeclared two-session diversity check merely
because every row was labelled ``ROLLING_5M``.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

import pandas as pd

import v13_volatility_squeeze_release_study as base


def utc_time_block(value: pd.Timestamp) -> str:
    hour = pd.Timestamp(value).tz_convert("UTC").hour
    if 0 <= hour < 8:
        return "ASIA_0000_0759_UTC"
    if 8 <= hour < 13:
        return "EUROPE_0800_1259_UTC"
    if 13 <= hour < 21:
        return "NEW_YORK_1300_2059_UTC"
    return "LATE_2100_2359_UTC"


def records(scored: list[base.ScoredSqueeze]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in scored:
        rows.append(
            {
                **asdict(item.candidate),
                "session": utc_time_block(item.candidate.entry_ts),
                "exit_ts": item.exit_ts,
                "exit_reason": item.exit_reason,
                "exit_price": item.exit_price,
                "net_return": item.net_return,
                "net_r": item.net_r,
                "mfe": item.mfe,
                "mae": item.mae,
            },
        )
    return pd.DataFrame(rows)


base.records = records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = base.run(args.cache.resolve(), args.output.resolve())
    result["time_block_attribution"] = {
        "blocks": [
            "ASIA_0000_0759_UTC",
            "EUROPE_0800_1259_UTC",
            "NEW_YORK_1300_2059_UTC",
            "LATE_2100_2359_UTC",
        ],
        "role": "robustness attribution only",
        "economic_logic_changed": False,
    }
    base.write_json(args.output / "study.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
