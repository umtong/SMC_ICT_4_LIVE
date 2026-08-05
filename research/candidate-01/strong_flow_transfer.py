#!/usr/bin/env python3
"""Test strong reversal displacement across assets and auction horizons.

The BTC development year and every quarter independently showed that reversal
plans with strongly aligned aggressive flow were the only fixed-range subset
with positive expectancy.  This runner freezes that causal condition and asks
whether it transfers without symbol-specific tuning to ETH, SOL, XRP and other
clock-auction horizons under the project's one-global-position constraint.

It uses the same one-bar delay, cost stress, structural stop/target, NAV risk
sizing, and mark-to-market drawdown calculations as ``portfolio_probe``.  This
is still a fast research probe; any accepted portfolio must be replayed through
the real NautilusTrader order/accounting path.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta
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
from data import parse_utc_date  # noqa: E402
from portfolio_probe import (  # noqa: E402
    Variant,
    _aggregate_variant,
    _atomic_json,
    _load_segment,
    simulate,
)


RISK_RATES = (0.01, 0.02, 0.03, 0.04, 0.05)
ALL_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
EXPERIMENTS = (
    (Variant("btc-240", ("BTCUSDT",), (240,)), 1.70),
    (Variant("multiasset-240-flow-150", ALL_SYMBOLS, (240,)), 1.50),
    (Variant("multiasset-240-flow-170", ALL_SYMBOLS, (240,)), 1.70),
    (Variant("multiasset-240-flow-200", ALL_SYMBOLS, (240,)), 2.00),
    (Variant("multiasset-120-240-flow-170", ALL_SYMBOLS, (120, 240)), 1.70),
    (Variant("multiasset-multiscale-flow-170", ALL_SYMBOLS, (60, 120, 240, 480)), 1.70),
)


def _segments(research: dict[str, Any]) -> list[tuple[str, datetime, datetime, str]]:
    def week(label: str, value: str) -> tuple[str, datetime, datetime, str]:
        start = parse_utc_date(value)
        return label, start, start + timedelta(days=7), "quick"

    return [
        week("discovery", str(research["discovery_week"])),
        *[
            week(f"confirmation-{index + 1}", value)
            for index, value in enumerate(research["confirmation_weeks"])
        ],
        (
            "long-evaluation",
            parse_utc_date(str(research["long_start"])),
            parse_utc_date(str(research["long_end"])),
            "development",
        ),
    ]


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    max_horizon = max(value for variant, _ in EXPERIMENTS for value in variant.horizons)
    warmup = max(int(research.get("warmup_minutes", 420)), max_horizon + 180)
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    rows: dict[str, list[dict[str, Any]]] = {
        variant.name: [] for variant, _ in EXPERIMENTS
    }
    manifests: list[dict[str, Any]] = []

    for label, start, end, role in _segments(research):
        bars_by_symbol, records = _load_segment(
            symbols=ALL_SYMBOLS,
            start=start,
            end=end,
            cache_dir=args.cache,
            warmup_minutes=warmup,
        )
        manifests.extend(asdict(record) for record in records)
        for variant, threshold in EXPERIMENTS:
            trades, metrics, daily = simulate(
                variant=variant,
                bars_by_symbol=bars_by_symbol,
                evaluation_start=start,
                evaluation_end=end,
                base_candidate=candidate,
                cost=cost,
                minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
                minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
                starting_nav=float(execution["starting_nav"]),
                risk_rates=RISK_RATES,
                minimum_trade_direction_flow_z=threshold,
            )
            metrics["role"] = role
            metrics["minimum_trade_direction_flow_z"] = threshold
            destination = output / variant.name / label
            destination.mkdir(parents=True, exist_ok=True)
            trades.to_csv(destination / "trades.csv", index=False)
            _atomic_json(destination / "metrics.json", metrics)
            for risk, values in daily.items():
                pd.DataFrame(values).to_csv(
                    destination / f"daily_nav_{risk:.4f}.csv",
                    index=False,
                )
            rows[variant.name].append(metrics)

    summary: dict[str, Any] = {}
    for variant, threshold in EXPERIMENTS:
        all_rows = rows[variant.name]
        quick_rows = [row for row in all_rows if row["role"] == "quick"]
        development_rows = [row for row in all_rows if row["role"] == "development"]
        quick = _aggregate_variant(quick_rows, RISK_RATES)
        development = _aggregate_variant(development_rows, RISK_RATES)
        combined = _aggregate_variant(all_rows, RISK_RATES)
        payload = {
            "variant": variant.name,
            "symbols": list(variant.symbols),
            "horizons": list(variant.horizons),
            "minimum_trade_direction_flow_z": threshold,
            "quick": quick,
            "development": development,
            "combined": combined,
        }
        _atomic_json(output / variant.name / "aggregate_metrics.json", payload)
        summary[variant.name] = payload

    unique = pd.DataFrame(manifests).drop_duplicates(["symbol", "month"])
    _atomic_json(
        output / "download_manifest.json",
        {"provider": "Binance Vision", "records": unique.to_dict(orient="records")},
    )
    _atomic_json(output / "strong_flow_transfer_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-strong-flow")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-strong-flow")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
