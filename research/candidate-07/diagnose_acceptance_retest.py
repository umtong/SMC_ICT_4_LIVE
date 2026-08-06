#!/usr/bin/env python3
"""Diagnose retest entries after directional external-liquidity acceptance.

The acceptance and hold states are taken from the preceding diagnostic. This
script asks a different causal question: before the accepted pool is reclaimed,
does price return to the broken boundary and reject it again with matching
aggressor flow? No orders, fills, PnL, or hypothetical NAV are produced.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import json
from pathlib import Path
from typing import Any

import pandas as pd

from data_flow import load_flow_bundle
from diagnose_external_acceptance import target_candidates
from diagnose_failed_flow import aggregate_flow, read_json, target_outcome
from smc_ict_4.manifest import write_json_atomic


def locate_index(bars: pd.DataFrame, timestamp_ns: int) -> int:
    matches = bars.index[bars["timestamp_ns"] == timestamp_ns].tolist()
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one signal bar for {timestamp_ns}, found {matches}"
        )
    return int(matches[0])


def diagnose_one(
    source: dict[str, Any],
    bars: pd.DataFrame,
    flow_logic: dict[str, Any],
    lookahead_bars: int,
) -> dict[str, Any]:
    direction = str(source["direction"])
    level = float(source["liquidity_level"])
    lower = float(source["lower_range"])
    upper = float(source["upper_range"])
    atr = float(source["contact"]["atr"])
    hold_index = locate_index(bars, int(source["hold"]["timestamp_ns"]))
    tolerance = float(flow_logic["reclaim_buffer_atr"]) * atr
    stop_buffer = float(flow_logic["stop_buffer_atr"]) * atr
    maximum_wait = int(flow_logic["confirmation_bars"])
    minimum_flow = float(flow_logic["confirmation_min_imbalance"])
    result: dict[str, Any] = {
        "scenario_id": f"{source['scenario_id']}-retest",
        "source_scenario_id": source["scenario_id"],
        "direction": direction,
        "liquidity_level": level,
        "maximum_wait_bars": maximum_wait,
        "outcome": "RETEST_TIMEOUT",
        "observations": [],
    }
    retest_index: int | None = None
    for index in range(
        hold_index + 1,
        min(len(bars), hold_index + 1 + maximum_wait),
    ):
        row = bars.iloc[index]
        close = float(row["close"])
        invalid = (
            close < level - tolerance
            if direction == "LONG"
            else close > level + tolerance
        )
        touch = (
            float(row["low"]) <= level + tolerance
            if direction == "LONG"
            else float(row["high"]) >= level - tolerance
        )
        directional_close = (
            close > float(row["open"])
            if direction == "LONG"
            else close < float(row["open"])
        )
        matching_flow = (
            float(row["imbalance"]) >= minimum_flow
            if direction == "LONG"
            else float(row["imbalance"]) <= -minimum_flow
        )
        record = {
            "timestamp_ns": int(row["timestamp_ns"]),
            "timestamp": row["timestamp"].isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": close,
            "imbalance": float(row["imbalance"]),
            "flow_z": float(row["flow_z"]),
            "touch": touch,
            "directional_close": directional_close,
            "matching_flow": matching_flow,
            "invalid": invalid,
        }
        result["observations"].append(record)
        if invalid:
            result["outcome"] = "BROKEN_POOL_RECLAIMED"
            return result
        if touch and directional_close and matching_flow:
            retest_index = index
            result["outcome"] = "RETEST_REJECTION_CONFIRMED"
            result["retest"] = record
            break
    if retest_index is None:
        return result

    row = bars.iloc[retest_index]
    entry = float(row["close"])
    stop = (
        level - stop_buffer
        if direction == "LONG"
        else level + stop_buffer
    )
    risk = entry - stop if direction == "LONG" else stop - entry
    result.update(
        {
            "entry": entry,
            "stop": stop,
            "risk": risk,
            "risk_atr": risk / atr if atr > 0 else None,
        }
    )
    if risk <= 0:
        result["outcome"] = "NONPOSITIVE_RETEST_RISK"
        return result

    future = bars.iloc[retest_index + 1 : retest_index + 1 + lookahead_bars]
    candidates = target_candidates(
        bars,
        retest_index,
        direction=direction,
        entry=entry,
        risk=risk,
        lower=lower,
        upper=upper,
    )
    minimum_rr = float(flow_logic["minimum_rr"])
    result["targets"] = {}
    for label, target in candidates.items():
        rr = abs(target - entry) / risk
        record: dict[str, Any] = {"price": target, "rr": rr}
        if (
            label not in {"1.0R", "1.5R", "2.0R", "3.0R"}
            and rr < minimum_rr
        ):
            record.update(
                {"outcome": "BELOW_MINIMUM_RR", "timestamp_ns": None}
            )
        else:
            record.update(target_outcome(future, direction, stop, target))
        result["targets"][label] = record
    return result


def run(args: argparse.Namespace) -> int:
    config = read_json(args.config)
    plan = read_json(args.week_plan)
    week = next(item for item in plan["weeks"] if item["stage"] == args.stage)
    start = date.fromisoformat(str(week["start"]))
    end = date.fromisoformat(str(week["end"]))
    stage_dir = args.output.resolve() / args.stage
    external = read_json(stage_dir / "external_acceptance_diagnostic.json")
    sources = [
        dict(item)
        for item in external["scenarios"]
        if item["outcome"] == "OUTSIDE_ACCEPTANCE_HELD"
    ]
    bundle = load_flow_bundle(
        symbol=str(config["symbol"]),
        trade_start=start,
        trade_end=end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=stage_dir / "acceptance_retest_data_manifest.json",
    )
    flow_logic = dict(config["flow_logic"])
    bars = aggregate_flow(
        bundle.frame,
        int(flow_logic["signal_minutes"]),
        int(flow_logic["flow_period"]),
    )
    scenarios = [
        diagnose_one(source, bars, flow_logic, args.lookahead_bars)
        for source in sources
    ]
    outcomes = Counter(str(item["outcome"]) for item in scenarios)
    target_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for item in scenarios:
        for label, target in (item.get("targets") or {}).items():
            target_counts[label][str(target["outcome"])] += 1
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "purpose": (
            "accepted-pool retest diagnostic; no orders or hypothetical NAV"
        ),
        "logic": [
            "directional external acceptance and one completed hold already occurred",
            "price retests the broken pool within the predeclared confirmation horizon",
            "no completed close reclaims the pool",
            "the retest bar closes back in the accepted direction with matching aggressor flow",
        ],
        "outcome_counts": dict(sorted(outcomes.items())),
        "target_outcome_counts": {
            label: dict(sorted(counts.items()))
            for label, counts in sorted(target_counts.items())
        },
        "scenarios": scenarios,
    }
    write_json_atomic(
        stage_dir / "acceptance_retest_diagnostic.json",
        payload,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    result.add_argument(
        "--week-plan",
        type=Path,
        default=candidate_dir / "week_plan.json",
    )
    result.add_argument("--stage", default="week-1")
    result.add_argument("--output", type=Path, required=True)
    result.add_argument(
        "--data-root",
        type=Path,
        default=Path(".research-data/candidate-07"),
    )
    result.add_argument("--lookahead-bars", type=int, default=24)
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
