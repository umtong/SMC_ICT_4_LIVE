#!/usr/bin/env python3
"""Run the frozen Candidate 13 mechanism on one continuous multi-year account."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "source"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from evidence_audit import audit
from run_leadership_scdam import run


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
    evaluation_days = (
        pd.Timestamp(end_exclusive) - pd.Timestamp(start)
    ).days
    output = ROOT / "result"
    output.mkdir(parents=True, exist_ok=True)

    config = json.loads((SOURCE / "base_config.json").read_text(encoding="utf-8"))
    config["candidate"] = "candidate-09-c13-continuous-price-discovery"
    config["selection"]["warmup_days"] = 3
    config["selection"]["evaluation_days"] = evaluation_days
    config["selection"]["weeks"] = {
        "ALL": {
            "start": start,
            "end_exclusive": end_exclusive,
        }
    }
    effective = ROOT / "continuous_config.json"
    write_json(effective, config)

    metrics = run(effective, "ALL", output)
    audit_result = audit(output, "ALL")
    write_json(output / "audit.json", audit_result)

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
        positive = sum(value for value in pnls if value > 0.0)
        largest_profit_share = (
            max((value for value in pnls if value > 0.0), default=0.0) / positive
            if positive > 0.0
            else 0.0
        )

    minimum_trades = (evaluation_days + 1) // 2
    safety_keys = (
        "evidence_complete",
        "metric_recalculation_passed",
        "risk_budget_passed",
        "global_slot_passed",
        "partial_entry_protection_passed",
        "no_liquidation_passed",
        "engine_errors_absent",
    )
    safety_passed = all(audit_result.get(key) is True for key in safety_keys)
    checks = {
        "single_continuous_nautilus_account": True,
        "implementation_and_evidence": safety_passed,
        "daily_geometric_growth": float(metrics["daily_geometric_growth"]) >= 0.01,
        "minimum_total_trades": int(metrics["closed_trades"]) >= minimum_trades,
        "minimum_active_months": active_months >= 36,
        "recoverable_drawdown": float(metrics["closed_trade_max_drawdown"]) <= 0.30,
        "profit_not_single_trade_dominated": largest_profit_share <= 0.35,
        "no_global_overlap": int(metrics.get("global_slot_overlap_count", 0)) == 0,
        "no_liquidation": not bool(metrics.get("liquidation_detected", False)),
        "positive_expectancy": sum(pnls) > 0.0,
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
        "audit_classification": audit_result.get("classification"),
        "transport": "monthly byte-equivalent Binance Vision klines; parity checked against daily source before execution",
        "logic_source": "frozen candidate-13 dynamic price-discovery auction transfer",
    }
    write_json(output / "FINAL_DECISION.json", decision)
    print(json.dumps(decision, indent=2, sort_keys=True, default=str))
    return 0 if safety_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
