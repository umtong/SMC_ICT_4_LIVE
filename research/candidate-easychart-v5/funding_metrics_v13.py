"""Funding-adjusted trade and account metrics for the external v13 ledger."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from backtest_support import write_json
from robustness_v8 import trade_robustness_metrics


FUNDING_TRADE_AUDIT_FILE = "trade_audit_funding_adjusted.csv"


def settlement_cash_flows(event_log: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    seen: set[tuple[str, int]] = set()
    for event in event_log:
        if event.get("kind") != "external_funding_settlement":
            continue
        position_id = str(event["position_id"])
        event_time_ns = int(event["event_time_ns"])
        key = (position_id, event_time_ns)
        if key in seen:
            raise ValueError(f"duplicate external funding settlement: {key}")
        seen.add(key)
        rows.append(
            {
                "position_id": position_id,
                "event_time_ns": event_time_ns,
                "funding_cash_flow": float(event["funding_cash_flow"]),
                "funding_rate": str(event["funding_rate"]),
                "mark_price": str(event["mark_price"]),
                "mark_age_ns": int(event["mark_age_ns"]),
            },
        )
    return pd.DataFrame(rows)


def funding_adjusted_trade_audit(
    trade_audit: pd.DataFrame,
    event_log: list[dict[str, Any]],
) -> pd.DataFrame:
    if "position_id" not in trade_audit.columns:
        raise ValueError("trade audit lacks position_id")
    settlements = settlement_cash_flows(event_log)
    output = trade_audit.copy()
    if settlements.empty:
        output["funding_cash_flow"] = 0.0
        output["funding_settlement_count"] = 0
    else:
        grouped = settlements.groupby("position_id", sort=False).agg(
            funding_cash_flow=("funding_cash_flow", "sum"),
            funding_settlement_count=("funding_cash_flow", "size"),
        )
        output = output.merge(grouped, on="position_id", how="left", validate="one_to_one")
        output["funding_cash_flow"] = output["funding_cash_flow"].fillna(0.0)
        output["funding_settlement_count"] = (
            output["funding_settlement_count"].fillna(0).astype(int)
        )
        unknown_positions = sorted(set(settlements["position_id"]) - set(output["position_id"]))
        if unknown_positions:
            raise ValueError(f"funding settlements lack closed trade audit rows: {unknown_positions}")

    realized = pd.to_numeric(output["realized_pnl"], errors="raise").astype(float)
    risk_budget = pd.to_numeric(output["risk_budget"], errors="raise").astype(float)
    output["funding_adjusted_realized_pnl"] = realized + output["funding_cash_flow"]
    output["funding_adjusted_actual_net_r"] = output["funding_adjusted_realized_pnl"] / risk_budget
    output["funding_cost_only"] = -output["funding_cash_flow"]
    return output


def apply_funding_adjustment(
    metrics: dict[str, Any],
    trade_audit: pd.DataFrame,
    event_log: list[dict[str, Any]],
    output: Path,
) -> dict[str, Any]:
    adjusted_audit = funding_adjusted_trade_audit(trade_audit, event_log)
    adjusted_audit.to_csv(output / FUNDING_TRADE_AUDIT_FILE, index=False)

    settlements = settlement_cash_flows(event_log)
    ledger_total = 0.0 if settlements.empty else float(settlements["funding_cash_flow"].sum())
    native_final_nav = float(metrics["final_nav"])
    starting_nav = float(metrics["starting_nav"])
    calendar_days = int(metrics["calendar_days"])
    adjusted_final_nav = native_final_nav + ledger_total
    if adjusted_final_nav <= 0.0:
        raise ValueError("funding-adjusted final NAV is non-positive")

    robustness_input = adjusted_audit.copy()
    robustness_input["realized_pnl"] = robustness_input["funding_adjusted_realized_pnl"]
    robustness_input["actual_net_r"] = robustness_input["funding_adjusted_actual_net_r"]
    adjusted_return = adjusted_final_nav / starting_nav - 1.0
    robustness = trade_robustness_metrics(
        robustness_input,
        reported_total_return=adjusted_return,
    )
    prefixed_robustness = {f"funding_adjusted_{key}": value for key, value in robustness.items()}

    result = dict(metrics)
    result.update(
        {
            "native_final_nav_before_external_funding": native_final_nav,
            "external_funding_cash_flow": ledger_total,
            "external_funding_settlement_count": int(len(settlements.index)),
            "funding_adjusted_final_nav": adjusted_final_nav,
            "funding_adjusted_total_return": adjusted_return,
            "funding_adjusted_daily_geometric_growth": (
                (adjusted_final_nav / starting_nav) ** (1.0 / calendar_days) - 1.0
            ),
            "funding_adjusted_profitable_trades": int(
                (adjusted_audit["funding_adjusted_realized_pnl"] > 0.0).sum()
            ),
            "funding_adjusted_losing_trades": int(
                (adjusted_audit["funding_adjusted_realized_pnl"] < 0.0).sum()
            ),
            "funding_adjusted_trade_audit": FUNDING_TRADE_AUDIT_FILE,
            **prefixed_robustness,
        },
    )
    write_json(output / "metrics_funding_adjusted.json", result)
    return result
