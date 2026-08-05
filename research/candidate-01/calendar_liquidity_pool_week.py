#!/usr/bin/env python3
"""Evaluate one BTC week of the frozen calendar-liquidity scenario.

This wrapper deliberately runs exactly one seven-day interval.  Detection and
execution logic are imported unchanged from ``calendar_liquidity_pool_probe``:
completed prior-day/prior-week high or low, causal five-minute sweep failure,
opposite-flow displacement through visible internal structure, next one-minute
market entry, structural stop, 7 bps per side and one global position.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from calendar_liquidity_pool_probe import (  # noqa: E402
    RULES,
    RISK_RATES,
    CalendarLiquidityPoolDetector,
)
from core import CandidateConfig  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
from portfolio_probe import Variant, simulate  # noqa: E402
from resting_liquidity_pool_probe import aggregate_five_minute  # noqa: E402


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    execution = dict(raw["execution"])
    start = parse_utc_date(args.week)
    end = start + timedelta(days=7)
    risk_rates = tuple(float(value) for value in args.risk_rates.split(","))
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0

    frame, records = load_interval(
        symbol="BTCUSDT",
        start=start,
        end=end,
        cache_dir=args.cache,
        warmup_minutes=10 * 24 * 60,
    )
    one_minute = to_auction_bars(frame)
    detector = CalendarLiquidityPoolDetector(candidate)
    for bar in aggregate_five_minute(frame):
        detector.on_bar(bar)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(asdict(row) for row in detector.evidence).to_csv(
        output / "evidence.csv",
        index=False,
    )
    atomic_json(output / "rejections.json", detector.rejections)
    results: dict[str, Any] = {}
    for rule in RULES:
        schedule = {
            timestamp: tuple(rows)
            for timestamp, rows in detector.schedules[rule].items()
        }
        trades, metrics, daily = simulate(
            variant=Variant(rule, ("BTCUSDT",), (60,)),
            bars_by_symbol={"BTCUSDT": one_minute},
            evaluation_start=start,
            evaluation_end=end,
            base_candidate=candidate,
            cost=cost,
            minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
            minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
            starting_nav=float(execution["starting_nav"]),
            risk_rates=risk_rates,
            allowed_scenario_ids=frozenset(),
            external_plans_by_signal_time=schedule,
        )
        destination = output / rule
        destination.mkdir(parents=True, exist_ok=True)
        trades.to_csv(destination / "trades.csv", index=False)
        atomic_json(destination / "metrics.json", metrics)
        for risk, rows in daily.items():
            pd.DataFrame(rows).to_csv(
                destination / f"daily_nav_{risk:.4f}.csv",
                index=False,
            )
        results[rule] = metrics

    payload = {
        "scenario": "prior-day and prior-week external-liquidity sweep failure",
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "structure_timeframe_minutes": 5,
        "execution_timeframe_minutes": 1,
        "long_evaluation_run": False,
        "one_global_position": True,
        "one_bar_execution_delay": True,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "plan_counts": {
            rule: sum(len(rows) for rows in detector.schedules[rule].values())
            for rule in RULES
        },
        "rejections": detector.rejections,
        "results": results,
        "downloads": [asdict(record) for record in records],
    }
    atomic_json(output / "calendar_liquidity_pool_week_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-calendar-pool-week",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-calendar-pool-week",
    )
    parser.add_argument(
        "--risk-rates",
        default=",".join(str(value) for value in RISK_RATES),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
