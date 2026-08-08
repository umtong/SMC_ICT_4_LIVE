#!/usr/bin/env python3
"""Compile the same completed micro-auction logic at 30m and 60m scales.

The user-visible failure after V56 was not lack of selectivity alone but lack of
independent day-trading opportunities.  This compiler does not loosen any
balance, breakout, inventory, retest or re-entry condition.  It evaluates the
same causal state machine on two natural nested auction horizons: half-hour and
hour.  Each scale uses its own completed balance and past-only rolling
thresholds; overlapping intents remain visible to the single-position
NautilusTrader strategy, which is the sole execution arbiter.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

import pandas as pd

import micro_auction_balance_transition_compiler as raw
import micro_auction_balance_transition_compiler_v2 as repaired
import rich_signal_compiler_v22 as v22


SCALES = (60, 30)
BASE_BALANCE_BARS = raw.BALANCE_BARS
BASE_MAX_AGE = raw.BALANCE_MAX_AGE_BARS
BASE_COOLDOWN = raw.COOLDOWN_BARS


def collect_multiscale(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Any], dict[str, Any]]:
    all_intents: list[Any] = []
    per_scale: dict[str, Any] = {}
    counts: Counter[str] = Counter()
    try:
        for scale in SCALES:
            raw.BALANCE_BARS = scale
            repaired.BALANCE_BARS = scale
            raw.BALANCE_MAX_AGE_BARS = max(BASE_MAX_AGE, int(round(scale * 1.5)))
            raw.COOLDOWN_BARS = max(BASE_COOLDOWN, int(round(scale * 0.4)))
            intents, summary = raw.collect_signals(
                data,
                evaluation_start,
                evaluation_end,
                config,
                impact_parameters,
                router,
            )
            for intent in intents:
                intent.details["micro_balance_bars"] = scale
                intent.details["micro_balance_horizon"] = f"{scale}m"
                intent.details["multiscale_compiler"] = (
                    "same causal state machine; no relaxed condition"
                )
            all_intents.extend(intents)
            per_scale[str(scale)] = summary
            counts[f"scale_{scale}_intents"] = len(intents)
    finally:
        raw.BALANCE_BARS = BASE_BALANCE_BARS
        repaired.BALANCE_BARS = BASE_BALANCE_BARS
        raw.BALANCE_MAX_AGE_BARS = BASE_MAX_AGE
        raw.COOLDOWN_BARS = BASE_COOLDOWN

    # Larger completed auctions are ordered first at an exact timestamp.  The
    # global Nautilus strategy still enforces one pending/open entry.
    all_intents.sort(
        key=lambda item: (
            int(item.signal_index),
            -int(item.details.get("micro_balance_bars", 0)),
            str(item.scenario),
        )
    )
    summary = {
        "candidate": "candidate-04-v59-nested-micro-auction",
        "compiler": "candidate-04-v59-multiscale-v1",
        "scales_minutes": list(SCALES),
        "counts": dict(counts),
        "per_scale": per_scale,
        "raw_intents": len(all_intents),
        "performance_calculated": False,
        "future_information_used": False,
        "market_logic_relaxed": False,
    }
    return all_intents, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--impact-config", type=Path, required=True)
    parser.add_argument("--router-config", type=Path, required=True)
    parser.add_argument("--rich-dir", type=Path, required=True)
    parser.add_argument("--kline-dir", type=Path, required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--download-klines", action="store_true")
    args = parser.parse_args()

    config = v22.Config.load(args.base_config)
    impact_parameters = v22.v10.ImpactParameters.load(args.impact_config)
    router = v22.RouterParameters.load(args.router_config)
    evaluation_start = pd.Timestamp(args.evaluation_start, tz="UTC")
    evaluation_end = (
        pd.Timestamp(args.evaluation_end, tz="UTC")
        + pd.Timedelta(hours=23, minutes=59)
    )
    data, nt_frame = v22._load_data(
        args.rich_dir,
        args.kline_dir,
        evaluation_start,
        evaluation_end,
        config,
        download_klines=args.download_klines,
    )
    intents, summary = collect_multiscale(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )
    v22.write_signals(
        args.output,
        intents,
        summary,
        data,
        nt_frame,
        evaluation_start,
        evaluation_end,
    )


if __name__ == "__main__":
    main()
