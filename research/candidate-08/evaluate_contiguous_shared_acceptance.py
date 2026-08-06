"""Evaluate one contiguous shared-account NautilusTrader run without replaying it."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

import pandas as pd


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(
        r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?",
        str(value).replace(",", ""),
    )
    return float(match.group()) if match else None


def _positive_pnls(path: Path) -> list[float]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    columns = [
        column
        for column in frame.columns
        if column.lower() in {"realized_pnl", "pnl", "realized_return"}
    ]
    if not columns:
        return []
    values: list[float] = []
    for raw in frame[columns[0]].tolist():
        value = _number(raw)
        if value is not None and value > 0:
            values.append(value)
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--stage", choices=("stage_c", "stage_d", "stage_e"), required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument(
        "--runner-kind",
        choices=("BASE", "BASE_ABLATION", "IMPLEMENTATION", "IMPLEMENTATION_ABLATION"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    stage_plan = plan[args.stage]
    gate = stage_plan["gate"]
    status_path = args.run.parent / "runner_exit_status.txt"
    if not status_path.exists():
        status_path = args.run / "runner_exit_status.txt"
    try:
        exit_status = int(status_path.read_text().strip())
    except Exception:
        exit_status = 999
    metrics_path = args.run / "metrics.json"
    if exit_status != 0 or not metrics_path.exists():
        summary = {
            "candidate": "candidate-08-aggtrade-acceptance-nautilus",
            "variant": args.variant,
            "runner_kind": args.runner_kind,
            "stage": args.stage,
            "engine": "NautilusTrader",
            "runner_exit_status": exit_status,
            "metrics_present": metrics_path.exists(),
            "gate_passed": False,
            "route": f"{args.stage.upper()}_IMPLEMENTATION_FAILURE",
        }
    else:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        positive_pnls = _positive_pnls(args.run / "positions.csv")
        positive_total = sum(positive_pnls)
        max_positive_share = (
            max(positive_pnls) / positive_total if positive_total else 0.0
        )
        intents_path = args.run / "trade_intents.json"
        intents = (
            json.loads(intents_path.read_text(encoding="utf-8")).get("trade_intents", [])
            if intents_path.exists()
            else []
        )
        assets = Counter(
            str(item["symbol"])
            for item in intents
            if item.get("symbol")
        )
        closed = int(metrics["position_metrics"]["closed_trades"])
        wins = int(metrics["position_metrics"]["wins"])
        positive_trade_share = wins / closed if closed else 0.0
        checks = {
            "minimum_daily_geometric_growth": (
                float(metrics["daily_geometric_growth"])
                >= float(gate["minimum_daily_geometric_growth"])
            ),
            "minimum_closed_trades": (
                closed >= int(gate["minimum_closed_trades"])
            ),
            "minimum_positive_trade_share": (
                positive_trade_share >= float(gate["minimum_positive_trade_share"])
            ),
            "maximum_single_positive_trade_pnl_share": (
                max_positive_share
                <= float(gate["maximum_single_positive_trade_pnl_share"])
            ),
            "minimum_assets_traded": (
                len(assets) >= int(gate["minimum_assets_traded"])
            ),
            "no_execution_failures": int(metrics["execution_failures"]) == 0,
            "no_residual_exposure": (
                int(metrics["open_positions_after_run"]) == 0
                and int(metrics["open_orders_after_run"]) == 0
            ),
            "no_unexpected_or_liquidation_closes": (
                int(metrics["unexpected_or_liquidation_closes"]) == 0
            ),
            "complete_multiasset_timestamps": (
                int(metrics["incomplete_multiasset_timestamps"]) == 0
            ),
        }
        passed = all(checks.values())
        if passed:
            route = {
                "stage_c": "RUN_CONTIGUOUS_STAGE_D",
                "stage_d": "RUN_CONTIGUOUS_STAGE_E",
                "stage_e": "PROJECT_GOAL_CANDIDATE_VERIFIED",
            }[args.stage]
        elif args.runner_kind.endswith("ABLATION"):
            route = "DISCARD_ACCEPTANCE_FAMILY"
        else:
            route = f"{args.stage.upper()}_LOGIC_FAILURE_UNABLATED"
        summary = {
            "candidate": "candidate-08-aggtrade-acceptance-nautilus",
            "variant": args.variant,
            "runner_kind": args.runner_kind,
            "stage": args.stage,
            "engine": "NautilusTrader",
            "window": metrics["window"],
            "calendar_days": metrics["calendar_days"],
            "starting_nav_usdt": metrics["starting_nav_usdt"],
            "final_nav_usdt": metrics["final_nav_usdt"],
            "nav_multiple": metrics["nav_multiple"],
            "total_return": metrics["total_return"],
            "daily_geometric_growth": metrics["daily_geometric_growth"],
            "goal_daily_geometric_growth": 0.01,
            "maximum_realized_equity_drawdown": metrics["maximum_realized_equity_drawdown"],
            "signals": metrics["signals_in_window"],
            "closed_trades": closed,
            "wins": wins,
            "win_rate": metrics["position_metrics"]["win_rate"],
            "positive_trade_share": positive_trade_share,
            "assets_traded": sorted(assets),
            "trade_intents_by_asset": dict(sorted(assets.items())),
            "maximum_single_positive_trade_pnl_share": max_positive_share,
            "gate_checks": checks,
            "gate_passed": passed,
            "route": route,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output.parent / "route.txt").write_text(
        summary["route"] + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
