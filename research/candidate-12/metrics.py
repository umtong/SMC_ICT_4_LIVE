"""Account-NAV and trade diagnostics derived from NautilusTrader reports."""
from __future__ import annotations

from decimal import Decimal
import json
import re
from typing import Any

import pandas as pd

def decimal_value(value: Any, default: Decimal | None = None) -> Decimal:
    if value is None:
        if default is None:
            raise ValueError("decimal value is unavailable")
        return default
    if hasattr(value, "as_decimal"):
        return Decimal(value.as_decimal())
    text = str(value).replace(",", "").strip()
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if match is None:
        if default is None:
            raise ValueError(f"cannot parse decimal from {value!r}")
        return default
    return Decimal(match.group(0))


def _closed_pnls(positions: pd.DataFrame) -> list[Decimal]:
    if positions.empty:
        return []
    closed = positions
    for column in ("ts_closed", "closed_time", "close_time"):
        if column in closed.columns:
            closed = closed[closed[column].notna()]
            break
    pnl_column = next(
        (column for column in ("realized_pnl", "realized_return", "pnl") if column in closed.columns),
        None,
    )
    if pnl_column is None:
        return []
    return [decimal_value(value, Decimal("0")) for value in closed[pnl_column].tolist()]


def _max_drawdown(starting_nav: Decimal, pnls: list[Decimal]) -> Decimal:
    equity = starting_nav
    peak = starting_nav
    maximum = Decimal("0")
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            maximum = max(maximum, (peak - equity) / peak)
    return maximum


def calculate_metrics(
    *,
    config: dict[str, Any],
    symbol: str,
    week_id: str,
    starting_nav: Decimal,
    final_nav: Decimal,
    positions: pd.DataFrame,
    orders: pd.DataFrame,
    plans: list[dict[str, Any]],
    logic: Any,
    errors: list[dict[str, Any]],
    lifecycle: list[dict[str, Any]],
    slot_rejections: int,
) -> dict[str, Any]:
    pnls = _closed_pnls(positions)
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    win_rate = len(wins) / len(pnls) if pnls else 0.0
    payoff_ratio: float | None = None
    if wins and losses:
        payoff_ratio = float((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)))
    elif wins:
        payoff_ratio = float("inf")
    evaluation_days = int(config["selection"]["evaluation_days"])
    if final_nav > 0:
        daily_growth = float((final_nav / starting_nav) ** (Decimal(1) / Decimal(evaluation_days)) - Decimal(1))
    else:
        daily_growth = -1.0
    max_drawdown = _max_drawdown(starting_nav, pnls)
    liquidation_detected = any("LIQUIDAT" in json.dumps(item, default=str).upper() for item in lifecycle)

    risk_pass = all(
        Decimal(str(plan["expected_total_loss"]))
        <= Decimal(str(plan["nav_before"])) * Decimal(str(config["account"]["risk_fraction"])) + Decimal("0.00000001")
        for plan in plans
    )
    scenario_breakdown: dict[str, int] = {}
    for plan in plans:
        scenario = str(plan["scenario"])
        scenario_breakdown[scenario] = scenario_breakdown.get(scenario, 0) + 1

    promise = config["gates"]["diagnostic_promise"]
    promise_pass = (
        len(pnls) >= int(promise["min_closed_trades"])
        and win_rate >= float(promise["min_win_rate"])
        and payoff_ratio is not None
        and payoff_ratio >= float(promise["min_payoff_ratio"])
        and daily_growth >= float(promise["min_daily_geometric_growth"])
        and risk_pass
        and not errors
        and not liquidation_detected
    )
    target = config["gates"]["project_target"]
    target_pass = (
        len(pnls) >= int(target["min_closed_trades_per_week"])
        and win_rate >= float(target["min_win_rate"])
        and payoff_ratio is not None
        and payoff_ratio >= float(target["min_payoff_ratio"])
        and daily_growth >= float(target["min_daily_geometric_growth"])
        and float(max_drawdown) <= float(target["max_closed_trade_drawdown"])
        and final_nav > 0
        and risk_pass
        and slot_rejections == 0
        and not errors
        and not liquidation_detected
    )
    return {
        "candidate": config["candidate"],
        "evidence_class": "NAUTILUS_ACCOUNT_NAV",
        "symbol": symbol,
        "week_id": week_id,
        "starting_nav": str(starting_nav),
        "final_nav": str(final_nav),
        "net_return": float(final_nav / starting_nav - 1),
        "daily_geometric_growth": daily_growth,
        "evaluation_calendar_days": evaluation_days,
        "closed_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "payoff_ratio": None if payoff_ratio in (None, float("inf")) else payoff_ratio,
        "all_closed_trades_won": bool(wins) and not losses,
        "closed_trade_max_drawdown": float(max_drawdown),
        "drawdown_scope": "closed-position realized PnL path; final account NAV is authoritative",
        "submitted_plans": len(plans),
        "scenario_plan_counts": scenario_breakdown,
        "detected_scenario_counts": dict(logic.scenario_counts),
        "pool_counts": dict(logic.pool_counts),
        "live_pools_at_end": len(logic.pools),
        "detected_events": len(logic.events),
        "skip_reasons": dict(logic.skips),
        "order_report_rows": len(orders.index),
        "position_report_rows": len(positions.index),
        "lifecycle_events": len(lifecycle),
        "risk_budget_passed": risk_pass,
        "global_slot_rejections": slot_rejections,
        "global_slot_passed": slot_rejections == 0,
        "liquidation_detected": liquidation_detected,
        "engine_errors": errors,
        "diagnostic_promise_gate_passed": promise_pass,
        "project_target_gate_passed": target_pass,
        "success_claim": target_pass,
    }

