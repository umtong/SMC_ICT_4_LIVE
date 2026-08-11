"""Metric calculation separated from the v3 event engine."""
from __future__ import annotations

import math
from typing import Any


def calculate_metrics(sim: Any, calendar_days: int) -> dict[str, object]:
    if calendar_days <= 0:
        raise ValueError("calendar_days must be positive")
    pnls = [trade.net_pnl for trade in sim.trades]
    wins = [value for value in pnls if value > 0.0]
    losses = [value for value in pnls if value < 0.0]
    marked = [sim.starting_nav, *(float(item["equity"]) for item in sim.equity)]
    peak = -math.inf
    max_drawdown = 0.0
    min_equity = math.inf
    for value in marked:
        peak = max(peak, value)
        min_equity = min(min_equity, value)
        if peak > 0.0:
            max_drawdown = max(max_drawdown, 1.0 - value / peak)
    final_equity = marked[-1] if marked else sim.nav
    geometric_daily = (
        (final_equity / sim.starting_nav) ** (1.0 / calendar_days) - 1.0
        if final_equity > 0.0
        else -1.0
    )
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    family: dict[str, dict[str, float | int]] = {}
    symbol: dict[str, dict[str, float | int]] = {}
    for trade in sim.trades:
        for key, container in ((trade.family, family), (trade.symbol, symbol)):
            bucket = container.setdefault(
                key,
                {"trades": 0, "wins": 0, "net_pnl": 0.0, "gross_r_sum": 0.0, "net_r_sum": 0.0},
            )
            bucket["trades"] = int(bucket["trades"]) + 1
            bucket["wins"] = int(bucket["wins"]) + int(trade.net_pnl > 0.0)
            bucket["net_pnl"] = float(bucket["net_pnl"]) + trade.net_pnl
            bucket["gross_r_sum"] = float(bucket["gross_r_sum"]) + trade.gross_r
            bucket["net_r_sum"] = float(bucket["net_r_sum"]) + trade.net_r
    for container in (family, symbol):
        for bucket in container.values():
            n = int(bucket["trades"])
            bucket["win_rate"] = int(bucket["wins"]) / n if n else 0.0
            bucket["mean_gross_r"] = float(bucket["gross_r_sum"]) / n if n else 0.0
            bucket["mean_net_r"] = float(bucket["net_r_sum"]) / n if n else 0.0
    gross_rs = [trade.gross_r for trade in sim.trades]
    cost_rs = [trade.cost_r for trade in sim.trades]
    net_rs = [trade.net_r for trade in sim.trades]
    return {
        "engine": "FAST_DIAGNOSTIC_NOT_AUTHORITATIVE_V3",
        "starting_nav": sim.starting_nav,
        "ending_nav": final_equity,
        "cash_nav": sim.nav,
        "open_position_at_end": sim.position is not None,
        "pending_setups_at_end": len(sim.pending),
        "total_return": final_equity / sim.starting_nav - 1.0,
        "geometric_daily_growth": geometric_daily,
        "calendar_days": calendar_days,
        "trades": len(sim.trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(sim.trades) if sim.trades else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
        "expectancy_usdt": sum(pnls) / len(pnls) if pnls else 0.0,
        "mean_gross_r": sum(gross_rs) / len(gross_rs) if gross_rs else 0.0,
        "mean_cost_r": sum(cost_rs) / len(cost_rs) if cost_rs else 0.0,
        "mean_net_r": sum(net_rs) / len(net_rs) if net_rs else 0.0,
        "max_drawdown": max_drawdown,
        "min_equity": min_equity,
        "largest_winner_share": max(wins, default=0.0) / gross_profit if gross_profit > 0.0 else 1.0,
        "family_metrics": family,
        "symbol_metrics": symbol,
        "diagnostics": dict(sim.diagnostics),
    }
