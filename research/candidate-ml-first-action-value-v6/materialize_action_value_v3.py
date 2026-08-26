#!/usr/bin/env python3
"""Materialize chronological action values with stable underscore-column access."""
from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    text = args.source.read_text(encoding="utf-8")
    old_periods = '    periods = sorted(variant_data["_period"].unique())\n'
    new_periods = '''    periods = (
        variant_data.groupby("_period", dropna=False)["_decision_time"]
        .min()
        .sort_values(kind="mergesort")
        .index.astype(str)
        .tolist()
    )
'''
    old_rows = '''    for row in scored.itertuples(index=False):
        state = str(getattr(row, "_state"))
        mech = str(getattr(row, "_mechanism"))
        frac_raw = getattr(row, "_target_fraction")
        frac = float(frac_raw) if pd.notna(frac_raw) else math.nan
'''
    new_rows = '''    for state_raw, mech_raw, frac_raw in zip(
        scored["_state"], scored["_mechanism"], scored["_target_fraction"], strict=True
    ):
        state = str(state_raw)
        mech = str(mech_raw)
        frac = float(frac_raw) if pd.notna(frac_raw) else math.nan
'''
    if old_periods not in text:
        raise RuntimeError("Chronology patch point not found")
    if old_rows not in text:
        raise RuntimeError("Stable row-access patch point not found")
    text = text.replace(old_periods, new_periods, 1)
    text = text.replace(old_rows, new_rows, 1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    compile(text, str(args.output), "exec")
    print(f"materialized action-value v3 at {args.output}")


if __name__ == "__main__":
    main()
