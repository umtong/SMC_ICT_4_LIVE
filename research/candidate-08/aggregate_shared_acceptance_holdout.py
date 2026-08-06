"""Aggregate independent NautilusTrader window evidence without simulating trades."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from math import exp, log
from pathlib import Path
import re
from typing import Any, Iterable

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


def _intent_assets(path: Path) -> list[str]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        str(item["symbol"])
        for item in payload.get("trade_intents", [])
        if item.get("symbol")
    ]


def _window_root(root: Path, name: str) -> Path:
    direct = root / name
    nested = direct / "run"
    return nested if (nested / "metrics.json").exists() else direct


def aggregate(
    *,
    root: Path,
    windows: Iterable[dict[str, Any]],
    gate: dict[str, Any],
    variant: str,
    runner_kind: str,
    stage: str,
) -> dict[str, Any]:
    expected = list(windows)
    metrics: list[dict[str, Any]] = []
    implementation_failures: list[dict[str, Any]] = []
    positive_pnls: list[float] = []
    traded_assets: list[str] = []

    for window in expected:
        name = str(window["name"])
        base = root / name
        run = _window_root(root, name)
        status_path = base / "runner_exit_status.txt"
        if not status_path.exists():
            status_path = run / "runner_exit_status.txt"
        try:
            status = int(status_path.read_text().strip())
        except Exception:
            status = 999
        metrics_path = run / "metrics.json"
        if status != 0 or not metrics_path.exists():
            implementation_failures.append(
                {
                    "name": name,
                    "runner_exit_status": status,
                    "metrics_present": metrics_path.exists(),
                }
            )
            continue
        item = json.loads(metrics_path.read_text(encoding="utf-8"))
        metrics.append(item)
        positive_pnls.extend(_positive_pnls(run / "positions.csv"))
        traded_assets.extend(_intent_assets(run / "trade_intents.json"))

    if implementation_failures or len(metrics) != len(expected):
        return {
            "candidate": "candidate-08-aggtrade-acceptance-nautilus",
            "variant": variant,
            "runner_kind": runner_kind,
            "stage": stage,
            "engine": "NautilusTrader",
            "implementation_failures": implementation_failures,
            "gate_passed": False,
            "route": f"{stage}_IMPLEMENTATION_FAILURE",
        }

    days = sum(float(item["calendar_days"]) for item in metrics)
    log_returns = [log(float(item["nav_multiple"])) for item in metrics]
    combined_growth = exp(sum(log_returns) / days) - 1.0
    positive_weeks = sum(float(item["total_return"]) > 0 for item in metrics)
    closed = [int(item["position_metrics"]["closed_trades"]) for item in metrics]
    wins = sum(int(item["position_metrics"]["wins"]) for item in metrics)
    total_closed = sum(closed)
    positive_trade_share = wins / total_closed if total_closed else 0.0
    positive_logs = [value for value in log_returns if value > 0]
    positive_log_total = sum(positive_logs)
    max_positive_week_share = (
        max(positive_logs) / positive_log_total if positive_log_total else 0.0
    )
    positive_pnl_total = sum(positive_pnls)
    max_positive_trade_share = (
        max(positive_pnls) / positive_pnl_total if positive_pnl_total else 0.0
    )
    asset_counts = Counter(traded_assets)

    checks: dict[str, bool] = {
        "minimum_combined_daily_geometric_growth": (
            combined_growth >= float(gate["minimum_combined_daily_geometric_growth"])
        ),
        "minimum_total_closed_trades": (
            total_closed >= int(gate["minimum_total_closed_trades"])
        ),
        "minimum_positive_trade_share": (
            positive_trade_share >= float(gate["minimum_positive_trade_share"])
        ),
        "minimum_assets_traded": (
            len(asset_counts) >= int(gate["minimum_assets_traded"])
        ),
        "maximum_single_positive_week_log_contribution": (
            max_positive_week_share
            <= float(gate["maximum_single_positive_week_log_contribution"])
        ),
        "maximum_single_positive_trade_pnl_share": (
            max_positive_trade_share
            <= float(gate["maximum_single_positive_trade_pnl_share"])
        ),
        "no_execution_failures": all(
            int(item["execution_failures"]) == 0 for item in metrics
        ),
        "no_residual_exposure": all(
            int(item["open_positions_after_run"]) == 0
            and int(item["open_orders_after_run"]) == 0
            for item in metrics
        ),
        "no_unexpected_or_liquidation_closes": all(
            int(item["unexpected_or_liquidation_closes"]) == 0
            for item in metrics
        ),
        "complete_multiasset_timestamps": all(
            int(item["incomplete_multiasset_timestamps"]) == 0
            for item in metrics
        ),
    }
    if "minimum_positive_weeks" in gate:
        checks["minimum_positive_weeks"] = (
            positive_weeks >= int(gate["minimum_positive_weeks"])
        )
    if "minimum_positive_weeks_of_twelve" in gate:
        checks["minimum_positive_weeks_of_twelve"] = (
            positive_weeks >= int(gate["minimum_positive_weeks_of_twelve"])
        )
    if "minimum_weeks_with_at_least_two_trades" in gate:
        checks["minimum_weeks_with_at_least_two_trades"] = (
            sum(value >= 2 for value in closed)
            >= int(gate["minimum_weeks_with_at_least_two_trades"])
        )

    passed = all(checks.values())
    ablated = runner_kind.endswith("ABLATION")
    if passed:
        route = "RUN_STAGE_B" if stage == "STAGE_A" else "PROMOTE_CONTIGUOUS_LONG"
    elif not ablated:
        route = f"{stage}_LOGIC_FAILURE_UNABLATED"
    else:
        route = "DISCARD_ACCEPTANCE_FAMILY"

    return {
        "candidate": "candidate-08-aggtrade-acceptance-nautilus",
        "variant": variant,
        "runner_kind": runner_kind,
        "stage": stage,
        "engine": "NautilusTrader",
        "calendar_days": days,
        "combined_daily_geometric_growth": combined_growth,
        "goal_daily_geometric_growth": 0.01,
        "positive_weeks": positive_weeks,
        "closed_trades_by_window": closed,
        "total_closed_trades": total_closed,
        "wins": wins,
        "positive_trade_share": positive_trade_share,
        "assets_traded": sorted(asset_counts),
        "trade_intents_by_asset": dict(sorted(asset_counts.items())),
        "maximum_single_positive_week_log_contribution": max_positive_week_share,
        "maximum_single_positive_trade_pnl_share": max_positive_trade_share,
        "gate_checks": checks,
        "gate_passed": passed,
        "route": route,
        "window_results": [
            {
                "name": item["window"]["name"],
                "signals": item["signals_in_window"],
                "closed_trades": item["position_metrics"]["closed_trades"],
                "wins": item["position_metrics"]["wins"],
                "win_rate": item["position_metrics"]["win_rate"],
                "final_nav_usdt": item["final_nav_usdt"],
                "total_return": item["total_return"],
                "daily_geometric_growth": item["daily_geometric_growth"],
                "maximum_realized_equity_drawdown": item["maximum_realized_equity_drawdown"],
            }
            for item in metrics
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--stage", choices=("STAGE_A", "STAGE_B"), required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument(
        "--runner-kind",
        choices=("BASE", "BASE_ABLATION", "IMPLEMENTATION", "IMPLEMENTATION_ABLATION"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    if args.stage == "STAGE_A":
        windows = plan["stage_a"]
        gate = plan["stage_a_gate"]
    else:
        windows = [*plan["stage_a"], *plan["stage_b"]]
        gate = plan["stage_b_combined_gate"]
    summary = aggregate(
        root=args.root,
        windows=windows,
        gate=gate,
        variant=args.variant,
        runner_kind=args.runner_kind,
        stage=args.stage,
    )
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
