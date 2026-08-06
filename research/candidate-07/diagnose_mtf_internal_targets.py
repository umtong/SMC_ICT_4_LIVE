#!/usr/bin/env python3
"""Diagnose causal five-minute internal targets for one-minute swing reversals.

The entry logic is frozen from ``diagnose_mtf_liquidity.py``. Only the target
mapping changes: a 15-minute external-liquidity sweep aims first at the nearest
five-minute swing liquidity which was confirmed and remained unconsumed before
entry. A five-minute pivot is known only after one completed bar on its right.

This script produces path evidence only and does not create orders, PnL, or NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Any

import pandas as pd

from data_flow import load_flow_bundle
from diagnose_failed_flow import read_json, target_outcome
from smc_ict_4.manifest import write_json_atomic


@dataclass(frozen=True, slots=True)
class InternalPool:
    pool_id: str
    side: str
    level: float
    pivot_ts_ns: int
    confirmed_ts_ns: int


def aggregate_context(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    work = frame.copy()
    work["bucket"] = work.index.floor(f"{minutes}min")
    grouped = work.groupby("bucket", sort=True)
    bars = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    )
    bars["timestamp_ns"] = grouped.apply(
        lambda part: int(part.index[-1].value),
        include_groups=False,
    )
    bars["timestamp"] = pd.to_datetime(
        bars["timestamp_ns"],
        unit="ns",
        utc=True,
    )
    return bars.reset_index(drop=True)


def confirmed_internal_pools(bars: pd.DataFrame) -> list[InternalPool]:
    pools: list[InternalPool] = []
    for center in range(1, len(bars) - 1):
        left = bars.iloc[center - 1]
        row = bars.iloc[center]
        right = bars.iloc[center + 1]
        confirmation_ts = int(right["timestamp_ns"])
        pivot_ts = int(row["timestamp_ns"])
        high = float(row["high"])
        low = float(row["low"])
        if high > float(left["high"]) and high > float(right["high"]):
            pools.append(
                InternalPool(
                    pool_id=f"5H-{pivot_ts}",
                    side="UPPER",
                    level=high,
                    pivot_ts_ns=pivot_ts,
                    confirmed_ts_ns=confirmation_ts,
                )
            )
        if low < float(left["low"]) and low < float(right["low"]):
            pools.append(
                InternalPool(
                    pool_id=f"5L-{pivot_ts}",
                    side="LOWER",
                    level=low,
                    pivot_ts_ns=pivot_ts,
                    confirmed_ts_ns=confirmation_ts,
                )
            )
    return pools


def unconsumed_before_entry(
    pool: InternalPool,
    minute: pd.DataFrame,
    entry_ts_ns: int,
) -> bool:
    timestamps = minute.index.map(lambda value: int(value.value))
    path = minute[
        (timestamps > pool.confirmed_ts_ns)
        & (timestamps < entry_ts_ns)
    ]
    if path.empty:
        return True
    if pool.side == "UPPER":
        return not bool((path["high"] >= pool.level).any())
    return not bool((path["low"] <= pool.level).any())


def diagnose_scenario(
    scenario: dict[str, Any],
    minute: pd.DataFrame,
    pools: list[InternalPool],
    max_hold_minutes: int,
) -> dict[str, Any]:
    entry_ts_ns = int(scenario["confirmation"]["timestamp_ns"])
    direction = str(scenario["direction"])
    entry = float(scenario["entry"])
    stop = float(scenario["stop"])
    risk = entry - stop if direction == "LONG" else stop - entry
    target_side = "UPPER" if direction == "LONG" else "LOWER"
    candidates = [
        pool
        for pool in pools
        if pool.side == target_side
        and pool.confirmed_ts_ns <= entry_ts_ns
        and (pool.level > entry if direction == "LONG" else pool.level < entry)
        and unconsumed_before_entry(pool, minute, entry_ts_ns)
    ]
    candidates.sort(
        key=lambda pool: pool.level,
        reverse=direction == "SHORT",
    )
    result: dict[str, Any] = {
        "scenario_id": scenario["scenario_id"],
        "direction": direction,
        "entry_timestamp_ns": entry_ts_ns,
        "entry": entry,
        "stop": stop,
        "risk": risk,
        "candidate_count": len(candidates),
        "targets": [],
    }
    if risk <= 0 or not candidates:
        result["selected_outcome"] = (
            "NONPOSITIVE_RISK" if risk <= 0 else "NO_CAUSAL_INTERNAL_POOL"
        )
        return result

    minute_ts = minute.index.map(lambda value: int(value.value))
    future = minute[
        (minute_ts > entry_ts_ns)
        & (
            minute_ts
            <= entry_ts_ns + max_hold_minutes * 60_000_000_000
        )
    ].copy()
    for number, pool in enumerate(candidates[:5], start=1):
        rr = abs(pool.level - entry) / risk
        outcome = target_outcome(
            pd.DataFrame(
                {
                    "timestamp_ns": future.index.map(
                        lambda value: int(value.value)
                    ),
                    "high": future["high"].to_numpy(),
                    "low": future["low"].to_numpy(),
                }
            ),
            direction,
            stop,
            pool.level,
        )
        result["targets"].append(
            {
                "rank": number,
                "pool_id": pool.pool_id,
                "price": pool.level,
                "rr": rr,
                **outcome,
            }
        )
    selected = result["targets"][0]
    result["selected_pool_id"] = selected["pool_id"]
    result["selected_rr"] = selected["rr"]
    result["selected_outcome"] = selected["outcome"]
    result["selected_timestamp_ns"] = selected["timestamp_ns"]
    return result


def run(args: argparse.Namespace) -> int:
    config = read_json(args.config)
    plan = read_json(args.week_plan)
    week = next(item for item in plan["weeks"] if item["stage"] == args.stage)
    start = date.fromisoformat(str(week["start"]))
    end = date.fromisoformat(str(week["end"]))
    stage_dir = args.output.resolve() / args.stage
    mtf = read_json(stage_dir / "mtf_liquidity_diagnostic.json")
    source = [
        dict(item)
        for item in mtf["scenarios"]
        if item.get("kind") == "SWING_POOL_REVERSAL"
        and item.get("targets")
    ]
    bundle = load_flow_bundle(
        symbol=str(config["symbol"]),
        trade_start=start,
        trade_end=end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=stage_dir / "mtf_internal_data_manifest.json",
    )
    five_minute = aggregate_context(bundle.frame, 5)
    pools = confirmed_internal_pools(five_minute)
    scenarios = [
        diagnose_scenario(
            scenario,
            bundle.frame,
            pools,
            int(config["max_hold_minutes"]),
        )
        for scenario in source
    ]
    selected_outcomes = Counter(
        str(item["selected_outcome"]) for item in scenarios
    )
    rr_bands = Counter()
    for item in scenarios:
        rr = item.get("selected_rr")
        if rr is None:
            continue
        if rr < 0.5:
            rr_bands["<0.5R"] += 1
        elif rr < 1.0:
            rr_bands["0.5-1.0R"] += 1
        elif rr < 1.5:
            rr_bands["1.0-1.5R"] += 1
        elif rr < 2.0:
            rr_bands["1.5-2.0R"] += 1
        else:
            rr_bands[">=2.0R"] += 1
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "purpose": (
            "causal five-minute internal-liquidity target diagnostic; "
            "entry logic frozen, no orders or hypothetical NAV"
        ),
        "source_reversal_routes": len(source),
        "confirmed_internal_pools": len(pools),
        "selected_outcomes": dict(sorted(selected_outcomes.items())),
        "selected_rr_bands": dict(sorted(rr_bands.items())),
        "logic": [
            "entry remains the confirmed one-minute reversal of a 15-minute external pool",
            "a five-minute internal pivot is known only after one completed right-side bar",
            "the target pool must remain unconsumed from confirmation through entry",
            "the nearest favorable internal pool is selected without a fitted R threshold",
        ],
        "scenarios": scenarios,
    }
    write_json_atomic(stage_dir / "mtf_internal_target_diagnostic.json", payload)
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
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
