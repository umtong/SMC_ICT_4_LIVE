#!/usr/bin/env python3
"""Download a frozen validation week and run candidate-06 in NautilusTrader."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import traceback
from typing import Any, Iterable, Mapping

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from market_data import load_week, write_quality  # noqa: E402
from nautilus_runner import run_nautilus_backtest  # noqa: E402
from smc_ict_4.event_log import write_events  # noqa: E402
from smc_ict_4.manifest import (  # noqa: E402
    build_data_manifest,
    create_run_manifest,
    write_data_manifest,
    write_json_atomic,
)


def _float(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "as_decimal"):
        return float(value.as_decimal())
    return float(value)


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _max_drawdown(values: Iterable[float]) -> float:
    peak = -math.inf
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0.0:
            worst = max(worst, (peak - value) / peak)
    return worst


def _daily_nav(samples: list[dict[str, Any]], starting_balance: float) -> tuple[list[dict[str, Any]], list[float]]:
    if not samples:
        return [], []
    frame = pd.DataFrame(samples)
    frame["timestamp"] = pd.to_datetime(frame["ts_ns"], unit="ns", utc=True)
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    frame["day"] = frame["timestamp"].dt.date.astype(str)
    closes = frame.groupby("day", sort=True)["nav"].last()
    previous = starting_balance
    rows: list[dict[str, Any]] = []
    returns: list[float] = []
    for day, nav in closes.items():
        value = float(nav)
        daily_return = value / previous - 1.0 if previous > 0.0 else -1.0
        rows.append({"day_utc": day, "nav": value, "return": daily_return})
        returns.append(daily_return)
        previous = value
    return rows, returns


def _breakdown(trades: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade.get(key, "UNKNOWN"))].append(trade)
    result: dict[str, dict[str, Any]] = {}
    for name, values in sorted(grouped.items()):
        pnls = [float(value["realized_pnl_after_cost"]) for value in values]
        rs = [float(value["realized_r_multiple"]) for value in values]
        result[name] = {
            "trades": len(values),
            "wins": sum(pnl > 0.0 for pnl in pnls),
            "win_rate": _safe_div(sum(pnl > 0.0 for pnl in pnls), len(values)),
            "pnl_after_cost": sum(pnls),
            "mean_r": _safe_div(sum(rs), len(rs)),
        }
    return result


def calculate_metrics(
    *,
    config: Mapping[str, Any],
    week_start: date,
    result: Any,
    rows: int,
) -> dict[str, Any]:
    strategy = result.strategy
    trades = list(strategy.closed_trades)
    starting = float(config["execution"]["starting_balance_usdt"])
    pnls = [float(trade["realized_pnl_after_cost"]) for trade in trades]
    r_multiples = [float(trade["realized_r_multiple"]) for trade in trades]
    realized = sum(pnls)
    ending = starting + realized
    evaluation_days = 7.0
    geometric_daily = (ending / starting) ** (1.0 / evaluation_days) - 1.0 if ending > 0.0 else -1.0
    wins = [value for value in pnls if value > 0.0]
    losses = [value for value in pnls if value < 0.0]
    profit_factor = sum(wins) / abs(sum(losses)) if losses else (math.inf if wins else 0.0)
    nav_values = [starting] + [float(sample["nav"]) for sample in strategy.equity_samples]
    daily_rows, daily_returns = _daily_nav(strategy.equity_samples, starting)
    positive_total = sum(wins)
    top_positive_share = max(wins) / positive_total if positive_total > 0.0 else 1.0
    top_three_share = sum(sorted(wins, reverse=True)[:3]) / positive_total if positive_total > 0.0 else 1.0
    estimated_explicit_cost = sum(
        float(trade["quantity"])
        * (float(trade["actual_entry_price"]) + float(trade["actual_exit_price"]))
        * float(trade["fee_rate_per_fill"])
        for trade in trades
    )
    estimated_tick_slippage = sum(2.0 * 0.1 * float(trade["quantity"]) for trade in trades)
    outcome_counts = Counter(str(trade["outcome"]) for trade in trades)
    signal_days = Counter(str(trade["signal_day_utc"]) for trade in trades)
    gate_cfg = config["gate"]

    metrics: dict[str, Any] = {
        "candidate": config["candidate"],
        "candidate_version": config["version"],
        "week_start_utc": week_start.isoformat(),
        "week_end_utc_exclusive": (week_start + pd.Timedelta(days=7)).date().isoformat(),
        "instrument_id": "BTCUSDT-PERP.BINANCE",
        "bar_interval": "1m",
        "bars": rows,
        "evaluation_days": evaluation_days,
        "starting_nav": starting,
        "ending_nav": ending,
        "net_pnl_after_cost": realized,
        "total_nav_return": ending / starting - 1.0,
        "geometric_daily_nav_growth": geometric_daily,
        "trades": len(trades),
        "trades_per_day": len(trades) / evaluation_days,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _safe_div(len(wins), len(trades)),
        "profit_factor": profit_factor,
        "mean_r_after_cost": _safe_div(sum(r_multiples), len(r_multiples)),
        "median_r_after_cost": float(pd.Series(r_multiples).median()) if r_multiples else 0.0,
        "mean_holding_minutes": _safe_div(
            sum(float(trade["duration_minutes"]) for trade in trades),
            len(trades),
        ),
        "max_drawdown_nav": _max_drawdown(nav_values),
        "worst_daily_return": min(daily_returns) if daily_returns else 0.0,
        "best_daily_return": max(daily_returns) if daily_returns else 0.0,
        "positive_days": sum(value > 0.0 for value in daily_returns),
        "negative_days": sum(value < 0.0 for value in daily_returns),
        "flat_days": sum(value == 0.0 for value in daily_returns),
        "days_with_closed_trades": len(signal_days),
        "largest_positive_trade_share": top_positive_share,
        "top_three_positive_trade_share": top_three_share,
        "estimated_explicit_fee_cost": estimated_explicit_cost,
        "estimated_one_tick_slippage_cost": estimated_tick_slippage,
        "scenario_breakdown": _breakdown(trades, "family"),
        "direction_breakdown": _breakdown(trades, "direction"),
        "hour_breakdown_utc": _breakdown(trades, "signal_hour_utc"),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "daily_nav": daily_rows,
        "diagnostics": strategy.diagnostics,
        "errors": list(strategy.errors),
        "execution_assumptions": {
            "risk_fraction_per_approved_trade": float(config["execution"]["risk_fraction"]),
            "effective_fee_rate_per_fill": float(config["execution"]["effective_fee_rate_per_fill"]),
            "one_tick_slippage_per_fill": bool(config["execution"]["one_tick_slippage_per_fill"]),
            "limit_touch_fill_probability": float(config["execution"]["prob_fill_on_limit_touch"]),
            "bar_path": "Nautilus adaptive high/low ordering",
            "position_accounting": "NautilusTrader position events; realized PnL includes commissions",
        },
    }

    gate_checks = {
        "geometric_daily_nav_growth": geometric_daily >= float(gate_cfg["minimum_geometric_daily_growth"]),
        "trade_count": len(trades) >= int(gate_cfg["minimum_trades"]),
        "win_rate": metrics["win_rate"] >= float(gate_cfg["minimum_win_rate"]),
        "max_drawdown": metrics["max_drawdown_nav"] <= float(gate_cfg["maximum_drawdown"]),
        "positive_trade_count": len(wins) >= int(gate_cfg["minimum_positive_trades"]),
        "profit_concentration": top_positive_share <= float(gate_cfg["maximum_largest_positive_trade_share"]),
        "no_runtime_errors": not strategy.errors,
        "direct_portfolio_nav": int(strategy.diagnostics["equity_query_fallbacks"]) == 0,
        "flat_at_end": strategy.portfolio.is_flat(strategy.config.instrument_id),
    }
    metrics["gate_checks"] = gate_checks
    metrics["gate_passed"] = all(gate_checks.values())
    metrics["gate_failures"] = [name for name, passed in gate_checks.items() if not passed]
    return metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(HERE / "config.json"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--week-index", type=int, default=0)
    parser.add_argument("--allow-gate-fail", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    weeks = [date.fromisoformat(value) for value in config["validation"]["frozen_week_starts_utc"]]
    if not 0 <= args.week_index < len(weeks):
        raise ValueError(f"week-index out of range: {args.week_index}")
    if args.week_index > 0 and config["validation"]["stage"] == "first_week":
        raise RuntimeError("later weeks are sealed until the first-week gate passes")
    week_start = weeks[args.week_index]
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_id = f"candidate-06-btc-{week_start.strftime('%Y%m%d')}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    data_root = Path(os.getenv("SMC4_RESEARCH_DATA", ".research-data/candidate-06")).resolve()

    try:
        loaded = load_week(config["validation"]["symbol"], week_start, data_root)
        quality_path = write_quality(output / "data_quality.json", loaded.quality)
        manifest = build_data_manifest(
            data_root,
            dataset=f"binance-futures-um-{config['validation']['symbol']}-1m-{week_start.isoformat()}",
            include=loaded.source_files,
            metadata_values={
                "week_start_utc": week_start.isoformat(),
                "observed_time_contract": loaded.quality["timestamp_contract"],
                "quality_report": str(quality_path),
            },
        )
        data_manifest_path = write_data_manifest(output / "data_manifest.json", manifest)
        result = run_nautilus_backtest(
            loaded.frame,
            config=config["execution"],
            logic_params=config["logic"],
        )
        result.fills.to_csv(output / "orders.csv", index=False)
        result.positions.to_csv(output / "positions.csv", index=False)
        result.account.to_csv(output / "account.csv", index=False)
        write_events(output / "scenario_events.jsonl", result.strategy.events)
        pd.DataFrame(result.strategy.closed_trades).to_csv(output / "trades.csv", index=False)
        pd.DataFrame(result.strategy.equity_samples).to_csv(output / "equity.csv", index=False)
        write_json_atomic(output / "trades.json", {"trades": result.strategy.closed_trades})
        metrics = calculate_metrics(
            config=config,
            week_start=week_start,
            result=result,
            rows=len(loaded.frame),
        )
        write_json_atomic(output / "metrics.json", metrics)
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate=config["candidate"],
                config_path=config_path,
                data_manifest_path=data_manifest_path,
                extra={
                    "week_index": args.week_index,
                    "week_start_utc": week_start.isoformat(),
                    "frozen_week_starts_utc": config["validation"]["frozen_week_starts_utc"],
                    "validation_stage": config["validation"]["stage"],
                    "nautilus_only": True,
                },
            ),
        )
        if metrics["errors"]:
            (output / "errors.log").write_text("\n".join(metrics["errors"]) + "\n", encoding="utf-8")
        compact = json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False)
        print(f"CANDIDATE06_METRICS_JSON={compact}")
        print(f"CANDIDATE06_GATE={'PASS' if metrics['gate_passed'] else 'FAIL'}")
        if not metrics["gate_passed"] and not args.allow_gate_fail:
            return 2
        return 0
    except Exception:
        trace = traceback.format_exc()
        (output / "errors.log").write_text(trace, encoding="utf-8")
        write_json_atomic(
            output / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate=config.get("candidate", "candidate-06"),
                config_path=config_path,
                extra={
                    "week_index": args.week_index,
                    "week_start_utc": week_start.isoformat(),
                    "status": "exception",
                    "nautilus_only": True,
                },
            ),
        )
        print(trace, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
