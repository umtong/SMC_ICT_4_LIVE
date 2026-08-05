#!/usr/bin/env python3
"""First-week variable-control test for LTF MSS confirmation latency.

The repeated five-minute liquidity pools, pool sweep, frozen one-minute pivot,
MSS definition, structural stop, opposing-pool target, next-minute execution,
fees, risk sizing and single-position accounting remain unchanged. Only the
maximum causal response window is varied across 10, 15, 20 and 30 minutes.
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

from core import CandidateConfig  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
import ltf_mss_resting_pool_probe as probe  # noqa: E402
from portfolio_probe import Variant, simulate  # noqa: E402
from resting_liquidity_pool_probe import aggregate_five_minute  # noqa: E402


WINDOWS = (10, 15, 20, 30)
RISK_RATE = 0.03
RULE = "ltf-mss-market"


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    start = parse_utc_date(str(research["discovery_week"]))
    end = start + timedelta(days=7)
    start_ns = int(pd.Timestamp(start).as_unit("ns").value)
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0

    frame, records = load_interval(
        symbol="BTCUSDT",
        start=start,
        end=end,
        cache_dir=args.cache,
        warmup_minutes=3 * 24 * 60,
    )
    minute_bars = to_auction_bars(frame)
    five_minute_map = {
        bar.ts_event_ns: bar for bar in aggregate_five_minute(frame)
    }
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}

    for window in WINDOWS:
        probe.MSS_EXPIRY_MINUTES = window
        detector = probe.LtfMssDetector(
            candidate,
            evaluation_start_ns=start_ns,
        )
        for minute_bar in minute_bars:
            five_minute_bar = five_minute_map.get(minute_bar.ts_event_ns)
            if five_minute_bar is not None:
                detector.on_five_minute(five_minute_bar)
            detector.on_one_minute(minute_bar)

        schedule = {
            timestamp: tuple(rows)
            for timestamp, rows in detector.schedules[RULE].items()
        }
        trades, metrics, daily = simulate(
            variant=Variant(f"{RULE}-{window}m", ("BTCUSDT",), (60,)),
            bars_by_symbol={"BTCUSDT": minute_bars},
            evaluation_start=start,
            evaluation_end=end,
            base_candidate=candidate,
            cost=cost,
            minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
            minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
            starting_nav=float(execution["starting_nav"]),
            risk_rates=(RISK_RATE,),
            allowed_scenario_ids=frozenset(),
            external_plans_by_signal_time=schedule,
        )
        destination = output / f"{window}m"
        destination.mkdir(parents=True, exist_ok=True)
        trades.to_csv(destination / "trades.csv", index=False)
        pd.DataFrame(asdict(row) for row in detector.mss_events).to_csv(
            destination / "mss_events.csv",
            index=False,
        )
        pd.DataFrame(asdict(row) for row in detector.retest_events).to_csv(
            destination / "retest_events.csv",
            index=False,
        )
        atomic_json(destination / "metrics.json", metrics)
        pd.DataFrame(daily[RISK_RATE]).to_csv(
            destination / "daily_nav.csv",
            index=False,
        )
        event_latencies = [row.latency_minutes for row in detector.mss_events]
        results[str(window)] = {
            "confirmation_window_minutes": window,
            "stage_counts": dict(detector.stage_counts),
            "rule_counts": dict(detector.rule_counts[RULE]),
            "mss_latency_minutes": event_latencies,
            "metrics": metrics,
        }

    payload = {
        "scenario": "LTF MSS latency variable-control test",
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "risk_fraction": RISK_RATE,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "fixed_rule": RULE,
        "windows_minutes": list(WINDOWS),
        "results": results,
        "downloads": [asdict(record) for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "ltf_mss_latency_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-hybrid-first-week",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-ltf-mss-latency",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
