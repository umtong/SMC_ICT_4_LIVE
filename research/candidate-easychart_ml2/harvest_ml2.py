#!/usr/bin/env python3
"""Label every ML2 plan by future target/stop first passage after execution."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
for candidate in (
    HERE,
    RESEARCH / "candidate-easychart_re1",
    RESEARCH / "candidate-easychart-v5",
    RESEARCH / "candidate-easychart-v4",
    RESEARCH / "candidate-easychart-v3",
):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from counterfactual_plan_harvest import HarvestConfig  # noqa: E402
from counterfactual_plan_harvest_fixed import harvest_counterfactual_plans  # noqa: E402
from fee_profiles_v5 import FEE_PROFILES  # noqa: E402
from instruments import CONTRACTS  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=list(CONTRACTS))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fee-profile", choices=tuple(FEE_PROFILES), required=True)
    parser.add_argument("--entry-slippage-ticks", type=int, default=2)
    parser.add_argument("--stop-slippage-ticks", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.end < args.start:
        raise SystemExit("--end must be >= --start")
    if args.warmup_days < 1:
        raise SystemExit("--warmup-days must be positive")
    symbols = tuple(args.symbols)
    unknown = sorted(set(symbols) - set(CONTRACTS))
    if unknown:
        raise SystemExit(f"unknown symbols: {unknown}")
    config = HarvestConfig(
        start=args.start,
        end=args.end,
        load_start=args.start - timedelta(days=args.warmup_days),
        symbols=symbols,
        cache=args.cache,
        output=args.output,
        fee_profile=args.fee_profile,
        entry_slippage_ticks=args.entry_slippage_ticks,
        stop_slippage_ticks=args.stop_slippage_ticks,
    )
    summary = harvest_counterfactual_plans(config)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
