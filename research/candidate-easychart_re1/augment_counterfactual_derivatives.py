#!/usr/bin/env python3
"""Attach point-in-time derivatives state to an existing answer sheet."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

from counterfactual_derivatives_features import (
    DERIVATIVES_MECHANISM_POLICY,
    DERIVATIVES_STATE_POLICY,
    build_derivatives_state,
)
from data_re1_flow import load_range_flow


def _plan_timestamp(row: pd.Series) -> pd.Timestamp:
    value = row.get("ts_ns", row.get("observed_time_ns"))
    if pd.isna(value):
        raise RuntimeError(f"missing plan timestamp for {row.get('plan_id')}")
    return pd.to_datetime(int(value), unit="ns", utc=True)


def augment(
    start: date,
    end: date,
    warmup_days: int,
    symbols: tuple[str, ...],
    cache: Path,
    output: Path,
) -> dict[str, object]:
    path = output / "counterfactual_plans.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    plans = pd.read_csv(path, low_memory=False)
    if plans.empty:
        return {
            "plans": 0,
            "derivatives_state_policy": DERIVATIVES_STATE_POLICY,
            "derivatives_mechanism_policy": DERIVATIVES_MECHANISM_POLICY,
        }

    load_start = start - timedelta(days=warmup_days)
    frames = {
        symbol: load_range_flow(symbol, load_start, end, cache)
        for symbol in symbols
    }
    state = build_derivatives_state(
        frames,
        load_start,
        end,
        cache,
    )
    feature_columns = [
        column
        for column in state.columns
        if column != "symbol"
    ]

    rows: list[dict[str, object]] = []
    missing = 0
    for _, plan in plans.iterrows():
        key = (str(plan["symbol"]), _plan_timestamp(plan))
        if key not in state.index:
            missing += 1
            rows.append({column: None for column in feature_columns})
            continue
        record = state.loc[key]
        if isinstance(record, pd.DataFrame):
            raise RuntimeError(f"duplicate derivatives state key {key}")
        rows.append(record[feature_columns].to_dict())

    augmented = pd.concat(
        [plans, pd.DataFrame(rows, index=plans.index)],
        axis=1,
    )
    backup = output / "counterfactual_plans_price_flow_only.csv"
    if not backup.exists():
        plans.to_csv(backup, index=False)
    augmented.to_csv(path, index=False)

    summary_path = output / "counterfactual_summary.json"
    summary = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.exists()
        else {}
    )
    extension = {
        "plans": int(len(augmented)),
        "derivatives_feature_count": len(feature_columns),
        "plans_missing_derivatives_state": missing,
        "derivatives_state_policy": DERIVATIVES_STATE_POLICY,
        "derivatives_mechanism_policy": DERIVATIVES_MECHANISM_POLICY,
    }
    summary["derivatives_extension"] = extension
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return extension


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = augment(
        args.start,
        args.end,
        args.warmup_days,
        tuple(args.symbols),
        args.cache,
        args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
