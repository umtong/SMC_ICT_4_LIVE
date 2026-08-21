#!/usr/bin/env python3
"""Attach exact shared online/offline causal market state to plan labels."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

from causal_state import STATE_FEATURES, STATE_POLICY, build_state_table
from data_re1_flow import load_range_flow


def _plan_time(row: pd.Series) -> pd.Timestamp:
    for name in ("ts_ns", "observed_time_ns"):
        value = row.get(name)
        if value is not None and not pd.isna(value):
            return pd.Timestamp(int(float(value)), unit="ns", tz="UTC")
    raise RuntimeError(f"missing plan time for {row.get('plan_id')}")


def augment(
    *,
    start: date,
    end: date,
    warmup_days: int,
    symbols: tuple[str, ...],
    cache: Path,
    output: Path,
) -> dict[str, object]:
    plans_path = output / "counterfactual_plans.csv"
    if not plans_path.exists():
        raise FileNotFoundError(plans_path)
    plans = pd.read_csv(plans_path, low_memory=False)
    if plans.empty:
        return {"plans": 0, "policy": STATE_POLICY, "feature_count": len(STATE_FEATURES)}
    duplicated = sorted(set(STATE_FEATURES).intersection(plans.columns))
    if duplicated:
        raise RuntimeError(f"counterfactual table already contains causal state columns: {duplicated[:10]}")

    load_start = start - timedelta(days=warmup_days)
    frames = {
        symbol: load_range_flow(symbol, load_start, end, cache)
        for symbol in symbols
    }
    state = build_state_table(frames)
    rows: list[dict[str, object]] = []
    missing = 0
    for _, plan in plans.iterrows():
        key = (str(plan["symbol"]), _plan_time(plan))
        if key not in state.index:
            missing += 1
            rows.append({name: None for name in STATE_FEATURES})
            continue
        record = state.loc[key]
        if isinstance(record, pd.DataFrame):
            raise RuntimeError(f"duplicate state key {key}")
        rows.append({name: record.get(name) for name in STATE_FEATURES})

    augmented = pd.concat([plans, pd.DataFrame(rows, index=plans.index)], axis=1)
    backup = output / "counterfactual_plans_without_ml_shared_state.csv"
    if not backup.exists():
        plans.to_csv(backup, index=False)
    augmented.to_csv(plans_path, index=False)

    extension = {
        "plans": int(len(plans)),
        "plans_missing_state": int(missing),
        "feature_count": len(STATE_FEATURES),
        "policy": STATE_POLICY,
    }
    summary_path = output / "counterfactual_summary.json"
    summary = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.exists()
        else {}
    )
    summary["ml_shared_causal_state"] = extension
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
    result = augment(
        start=args.start,
        end=args.end,
        warmup_days=args.warmup_days,
        symbols=tuple(args.symbols),
        cache=args.cache,
        output=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
