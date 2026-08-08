#!/usr/bin/env python3
"""Absolute account gate for Candidate 11 spot/perpetual candidates.

This script only audits NautilusTrader output.  It never creates orders, infers
fills, changes risk, or reconstructs PnL outside the engine.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd


def _read_optional_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _pnls(positions: pd.DataFrame) -> list[float]:
    if positions.empty:
        return []
    column = next((name for name in ("realized_pnl", "pnl") if name in positions), None)
    if column is None:
        return []
    values: list[float] = []
    for raw in positions[column].tolist():
        try:
            value = float(str(raw).strip().split()[0].replace(",", ""))
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _payoff_ratio(pnls: list[float]) -> float:
    wins = [value for value in pnls if value > 0.0]
    losses = [value for value in pnls if value < 0.0]
    if wins and losses:
        return (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
    return math.inf if wins else 0.0


def _ready_rows(features: pd.DataFrame) -> dict[str, int]:
    result: dict[str, int] = {}
    for column in (
        "feature_ready",
        "l1_pressure_feature_ready",
        "spot_perp_feature_ready",
    ):
        if column not in features:
            result[column] = 0
            continue
        values = features[column]
        if values.dtype == bool:
            ready = values
        else:
            ready = values.astype(str).str.strip().str.lower().isin(
                {"true", "1", "yes"},
            )
        result[column] = int(ready.sum())
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--minimum-trades", type=int, required=True)
    parser.add_argument("--minimum-win-rate", type=float, default=0.80)
    parser.add_argument("--minimum-payoff", type=float, default=1.20)
    parser.add_argument("--minimum-daily-growth", type=float, default=0.01)
    parser.add_argument("--maximum-drawdown", type=float, default=0.20)
    args = parser.parse_args()

    metrics = json.loads((args.root / "metrics.json").read_text(encoding="utf-8"))
    diagnostics = json.loads(
        (args.root / "strategy_diagnostics.json").read_text(encoding="utf-8"),
    )
    positions = _read_optional_csv(args.root / "positions.csv")
    features = _read_optional_csv(args.root / "features.csv.gz")
    pnls = _pnls(positions)
    payoff = _payoff_ratio(pnls)
    ready = _ready_rows(features)

    checks = {
        "feature_information_present": ready["feature_ready"] > 0,
        "l1_information_present": ready["l1_pressure_feature_ready"] > 0,
        "spot_perp_information_present": ready["spot_perp_feature_ready"] > 0,
        "minimum_closed_trades": int(metrics.get("trades", 0)) >= args.minimum_trades,
        "minimum_win_rate": float(metrics.get("win_rate", 0.0)) >= args.minimum_win_rate,
        "minimum_payoff_ratio": payoff >= args.minimum_payoff,
        "minimum_daily_geometric_growth": (
            float(metrics.get("geometric_daily_growth", 0.0))
            >= args.minimum_daily_growth
        ),
        "maximum_drawdown": (
            float(metrics.get("max_drawdown", 1.0)) <= args.maximum_drawdown
        ),
        "no_liquidation": int(metrics.get("liquidations", 0)) == 0,
        "no_order_rejections": int(diagnostics.get("order_rejections", 0)) == 0,
        "global_slot": (
            int(diagnostics.get("max_simultaneous_entry_intents", 99)) <= 1
            and int(diagnostics.get("max_open_positions_observed", 99)) <= 1
        ),
    }
    gate = all(checks.values())
    result = {
        "schema": args.schema,
        "classification": "ABSOLUTE_GATE_PASSED" if gate else "GOAL_NOT_MET",
        "gate_passed": gate,
        "success_claim": False,
        "checks": checks,
        "payoff_ratio": payoff,
        "ready_rows": ready,
        "metrics": metrics,
        "diagnostics": diagnostics,
        "funnel": {
            key: diagnostics.get(key, 0)
            for key in (
                "candidate16_parent_auctions",
                "candidate16_failed_auctions",
                "candidate16_acceptance_continuations",
                "candidate16_unresolved",
                "candidate16_v2_failure_frozen",
                "candidate16_v2_failure_initiatives",
                "candidate16_v2_acceptance_liquidity_confirmed",
                "candidate16_v4_pressure_observations",
                "entry_submissions",
                "spot_perp_interactions",
                "spot_perp_broad_attacks",
                "spot_perp_perp_only_attacks",
                "spot_perp_post_interaction_observations",
                "spot_perp_failed_state_rejections",
                "spot_perp_acceptance_state_rejections",
                "vacuum_interactions",
                "vacuum_broad_parent_attacks",
                "vacuum_immediate_persistence",
                "vacuum_immediate_rejections",
                "vacuum_entries",
            )
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=True))
    print(f"GATE={'true' if gate else 'false'}")


if __name__ == "__main__":
    main()
