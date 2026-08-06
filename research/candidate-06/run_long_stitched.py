"""Long BTC screen from 52 fixed NautilusTrader weekly runs.

This is deliberately a conservative promotion stage.  Every weekly PnL and fill
comes from the same NautilusTrader path as the short validation.  Week boundaries
are flat and primitive state is warmed again, so a passing result is subsequently
eligible for a single continuous-engine confirmation; a failing result is not.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def _replace_week_lists(config: dict[str, Any], start: str) -> None:
    validation = config.setdefault("validation", {})
    replaced = False
    for key, value in list(validation.items()):
        if isinstance(value, list) and value and all(isinstance(item, str) and len(item) >= 10 for item in value):
            validation[key] = [start]
            replaced = True
    for key in ("weeks", "week_starts", "week_starts_utc", "frozen_weeks", "frozen_week_starts"):
        if key in validation:
            validation[key] = [start]
            replaced = True
    if not replaced:
        validation["weeks"] = [start]


def _read_equity(path: Path) -> list[tuple[int, float]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        return []
    fields = rows[0].keys()
    ts_key = next((key for key in fields if key.lower() in {"ts_ns", "timestamp_ns", "event_time_ns"}), None)
    nav_key = next((key for key in fields if key.lower() in {"nav", "equity", "total_equity", "value"}), None)
    if ts_key is None or nav_key is None:
        return []
    return [(int(float(row[ts_key])), float(row[nav_key])) for row in rows if row.get(ts_key) and row.get(nav_key)]


def _read_trade_pnls(path: Path) -> list[float]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    candidates = ("realized_pnl_after_cost", "realized_pnl", "net_pnl", "pnl")
    result: list[float] = []
    for row in rows:
        key = next((name for name in candidates if row.get(name) not in (None, "")), None)
        if key is not None:
            result.append(float(row[key]))
    return result


def _max_drawdown(values: list[float]) -> float:
    peak = 0.0
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0.0:
            maximum = max(maximum, (peak - value) / peak)
    return maximum


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="2025-01-06")
    parser.add_argument("--weeks", type=int, default=52)
    args = parser.parse_args()

    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads(args.config.read_text(encoding="utf-8"))
    start_day = date.fromisoformat(args.start)

    cumulative_nav = float(base.get("execution", {}).get("starting_balance", 100000.0))
    initial_nav = cumulative_nav
    stitched_equity: list[tuple[int, float]] = []
    aggregate_pnls: list[float] = []
    week_records: list[dict[str, Any]] = []
    total_trades = wins = losses = 0
    runtime_failures: list[dict[str, Any]] = []

    for index in range(args.weeks):
        week_start = start_day + timedelta(days=7 * index)
        week_name = week_start.isoformat()
        week_dir = output / f"week-{index + 1:02d}-{week_name}"
        config = copy.deepcopy(base)
        _replace_week_lists(config, week_name)
        config.setdefault("validation", {})["stage"] = "long_stitched_2025"
        config_path = output / f"config-week-{index + 1:02d}.json"
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = [
            sys.executable,
            str(candidate_dir / "run_validation.py"),
            "--config",
            str(config_path),
            "--output",
            str(week_dir),
            "--week-index",
            "0",
            "--allow-gate-fail",
        ]
        completed = subprocess.run(command, cwd=repository, text=True, capture_output=True, check=False)
        metrics_path = week_dir / "metrics.json"
        if completed.returncode != 0 or not metrics_path.exists():
            runtime_failures.append(
                {
                    "week": week_name,
                    "returncode": completed.returncode,
                    "stdout_tail": completed.stdout[-4000:],
                    "stderr_tail": completed.stderr[-12000:],
                }
            )
            break
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        week_start_nav = float(metrics["starting_nav"])
        week_end_nav = float(metrics["ending_nav"])
        ratio = week_end_nav / week_start_nav if week_start_nav > 0.0 else 0.0
        prior_cumulative = cumulative_nav
        cumulative_nav *= ratio
        scale = prior_cumulative / week_start_nav if week_start_nav > 0.0 else 0.0
        equity_rows = _read_equity(week_dir / "equity.csv")
        if not equity_rows:
            equity_rows = [
                (int(index * 7 * 86400 * 1_000_000_000), prior_cumulative),
                (int((index + 1) * 7 * 86400 * 1_000_000_000), cumulative_nav),
            ]
        stitched_equity.extend((ts_ns, nav * scale) for ts_ns, nav in equity_rows)
        pnls = [value * scale for value in _read_trade_pnls(week_dir / "trades.csv")]
        aggregate_pnls.extend(pnls)
        total_trades += int(metrics.get("trades", 0))
        wins += int(metrics.get("wins", 0))
        losses += int(metrics.get("losses", 0))
        week_records.append(
            {
                "week": week_name,
                "returncode": completed.returncode,
                "start_nav_unscaled": week_start_nav,
                "end_nav_unscaled": week_end_nav,
                "weekly_return": ratio - 1.0,
                "stitched_start_nav": prior_cumulative,
                "stitched_end_nav": cumulative_nav,
                "trades": int(metrics.get("trades", 0)),
                "win_rate": float(metrics.get("win_rate", 0.0)),
                "max_drawdown_nav": float(metrics.get("max_drawdown_nav", 0.0)),
                "gate_passed_short_week": bool(metrics.get("gate_passed", False)),
                "gate_failures_short_week": metrics.get("gate_failures", []),
            }
        )

    days = len(week_records) * 7
    geometric_daily = (cumulative_nav / initial_nav) ** (1.0 / days) - 1.0 if days > 0 and cumulative_nav > 0.0 else -1.0
    nav_values = [value for _, value in stitched_equity]
    max_drawdown = _max_drawdown(nav_values)
    gross_profit = sum(value for value in aggregate_pnls if value > 0.0)
    gross_loss = -sum(value for value in aggregate_pnls if value < 0.0)
    profit_factor: float | str = gross_profit / gross_loss if gross_loss > 0.0 else ("Infinity" if gross_profit > 0.0 else 0.0)
    positive = sorted((value for value in aggregate_pnls if value > 0.0), reverse=True)
    largest_positive_share = positive[0] / sum(positive) if positive and sum(positive) > 0.0 else 1.0
    trades_per_day = total_trades / days if days > 0 else 0.0
    win_rate = wins / total_trades if total_trades > 0 else 0.0

    failures: list[str] = []
    if runtime_failures:
        failures.append("RUNTIME_FAILURE")
    if len(week_records) != args.weeks:
        failures.append("INCOMPLETE_PERIOD")
    if geometric_daily < 0.01:
        failures.append("GEOMETRIC_DAILY_NAV_GROWTH_BELOW_1_PERCENT")
    if trades_per_day < 1.0:
        failures.append("TRADES_PER_DAY_BELOW_1")
    if win_rate < 0.45:
        failures.append("WIN_RATE_BELOW_45_PERCENT")
    if max_drawdown > 0.25:
        failures.append("MAX_DRAWDOWN_ABOVE_25_PERCENT")
    if largest_positive_share > 0.10:
        failures.append("PROFIT_CONCENTRATION_ABOVE_10_PERCENT")

    summary = {
        "method": "52 fixed weekly NautilusTrader runs, flat boundaries, stitched proportional NAV",
        "candidate_config": str(args.config),
        "start_week": args.start,
        "requested_weeks": args.weeks,
        "completed_weeks": len(week_records),
        "calendar_days": days,
        "starting_nav": initial_nav,
        "ending_nav": cumulative_nav,
        "total_nav_return": cumulative_nav / initial_nav - 1.0 if initial_nav > 0.0 else -1.0,
        "geometric_daily_nav_growth": geometric_daily,
        "trades": total_trades,
        "trades_per_day": trades_per_day,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown_nav": max_drawdown,
        "largest_positive_trade_share": largest_positive_share,
        "gate_passed": not failures,
        "gate_failures": failures,
        "runtime_failures": runtime_failures,
        "weekly_results": week_records,
        "limitations": [
            "Primitive state and account ledger are reset at each flat week boundary.",
            "Percentage returns are stitched proportionally; a passing result requires a later single-engine continuous confirmation.",
            "All fills, fees, order lifecycle, and within-week NAV are produced by NautilusTrader.",
        ],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (output / "stitched_equity.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts_ns", "nav"])
        writer.writerows(stitched_equity)
    lines = [
        "# Candidate 06 long stitched BTC screen",
        "",
        "| metric | value |",
        "|---|---:|",
    ]
    for key in (
        "completed_weeks", "calendar_days", "starting_nav", "ending_nav", "total_nav_return",
        "geometric_daily_nav_growth", "trades", "trades_per_day", "win_rate", "profit_factor",
        "max_drawdown_nav", "largest_positive_trade_share", "gate_passed",
    ):
        lines.append(f"| `{key}` | `{summary[key]}` |")
    lines.extend(["", "## Gate failures", ""])
    lines.extend([f"- `{value}`" for value in failures] or ["- none"])
    lines.extend(["", "## Methodological status", "", "This is a promotion screen, not the final continuous-period proof."])
    (output / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
