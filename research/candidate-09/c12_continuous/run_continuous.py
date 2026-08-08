#!/usr/bin/env python3
"""Evaluate the frozen Candidate 12 I13 on one continuous BTC account."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "source"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from run import run


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def money(value: Any) -> float:
    text = str(value).strip()
    return float(text.split()[0]) if text else 0.0


def main() -> int:
    start = "2022-01-01"
    end_exclusive = "2026-01-01"
    evaluation_days = (pd.Timestamp(end_exclusive) - pd.Timestamp(start)).days
    output = ROOT / "result"
    output.mkdir(parents=True, exist_ok=True)

    config = json.loads((SOURCE / "config.json").read_text(encoding="utf-8"))
    config["candidate"] = "candidate-09-c12-i13-continuous"
    config["selection"]["evaluation_days"] = evaluation_days
    config["selection"]["weeks"] = {
        "ALL": {
            "start": start,
            "end_exclusive": end_exclusive,
        }
    }
    config["selection"]["selection_rule"] = (
        "Frozen Candidate 12 I13 source is evaluated once on one continuous "
        "2022-01-01 through 2026-01-01 BTCUSDT Nautilus account. No performance "
        "observation changes the source, thresholds, costs, risk, or dates."
    )
    effective = ROOT / "continuous_config.json"
    write_json(effective, config)

    data_link = output / "data"
    try:
        metrics = run(effective, "BTCUSDT", "ALL", output)
    finally:
        if data_link.is_symlink():
            data_link.unlink()

    positions_path = output / "positions.csv"
    positions = pd.read_csv(positions_path) if positions_path.is_file() else pd.DataFrame()
    if positions.empty:
        active_months = 0
        largest_profit_share = 0.0
        pnls: list[float] = []
    else:
        closed = pd.to_datetime(positions["ts_closed"], utc=True, errors="coerce")
        active_months = closed.dropna().dt.to_period("M").nunique()
        pnls = [money(value) for value in positions["realized_pnl"].tolist()]
        total_positive = sum(value for value in pnls if value > 0.0)
        largest_profit_share = (
            max((value for value in pnls if value > 0.0), default=0.0)
            / total_positive
            if total_positive > 0.0
            else 0.0
        )

    minimum_trades = (evaluation_days + 1) // 2
    checks = {
        "single_continuous_nautilus_account": True,
        "engine_errors_absent": metrics.get("engine_errors") == [],
        "event_log_valid": metrics.get("event_log_valid") is True,
        "risk_budget": metrics.get("risk_budget_passed") is True,
        "global_slot": metrics.get("global_slot_passed") is True,
        "no_liquidation": not bool(metrics.get("liquidation_detected", False)),
        "daily_geometric_growth": float(metrics["daily_geometric_growth"]) >= 0.01,
        "minimum_total_trades": int(metrics["closed_trades"]) >= minimum_trades,
        "minimum_active_months": active_months >= 36,
        "recoverable_drawdown": float(metrics["closed_trade_max_drawdown"]) <= 0.30,
        "profit_not_single_trade_dominated": largest_profit_share <= 0.35,
        "positive_net_return": float(metrics["net_return"]) > 0.0,
    }
    decision = {
        "candidate": config["candidate"],
        "status": "SUCCESS" if all(checks.values()) else "FAILED_CONTINUOUS_EVALUATION",
        "interval": {
            "start": start,
            "end_exclusive": end_exclusive,
            "calendar_days": evaluation_days,
        },
        "checks": checks,
        "minimum_total_trades_required": minimum_trades,
        "active_months": active_months,
        "maximum_single_trade_profit_share": largest_profit_share,
        "metrics": metrics,
        "source": "frozen Candidate 12 I13 state-priced reacceleration router",
        "transport": "monthly byte-equivalent Binance Vision klines, parity checked against daily source",
    }
    write_json(output / "FINAL_DECISION.json", decision)
    print(json.dumps(decision, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
