"""After-cost NAV and scenario diagnostics for ADSE-v1."""
from __future__ import annotations

from math import inf, log
from statistics import median
from typing import Any

from adse_model import AdseConfig


def build_metrics(
    config: AdseConfig,
    replay: dict[str, Any],
    *,
    week_start: str,
    week_end: str,
    data_stats: dict[str, Any],
) -> dict[str, Any]:
    trades = list(replay.pop("trades_detail"))
    final_nav = float(replay["final_nav"]); days = 7.0
    values = [float(trade["net_r"]) for trade in trades]
    positive = [value for value in values if value > 0]
    non_positive = [value for value in values if value <= 0]
    gross_profit = sum(positive); gross_loss = abs(sum(non_positive))
    daily_growth = (final_nav / config.initial_nav) ** (1.0 / days) - 1.0
    daily_rows: list[dict[str, Any]] = []; prior = config.initial_nav
    for row in replay["daily_equity"]:
        nav = float(row["nav"])
        daily_rows.append({"date": row["date"], "nav": nav, "return": nav / prior - 1.0})
        prior = nav
    win_rate = len(positive) / len(trades) if trades else 0.0
    mean_r = sum(values) / len(values) if values else 0.0
    gate_checks = {
        "enough_trades": len(trades) >= config.minimum_trades,
        "win_rate": win_rate >= config.minimum_win_rate,
        "positive_expectancy": mean_r > 0,
        "daily_geometric_growth": daily_growth >= config.minimum_daily_geometric_growth,
        "mark_to_market_drawdown": float(replay["max_drawdown"]) < config.maximum_mark_to_market_drawdown,
    }
    positive_sorted = sorted(positive, reverse=True)
    return {
        "candidate": config.candidate,
        "futures_instrument_id": config.futures_instrument_id,
        "spot_instrument_id": config.spot_instrument_id,
        "week_start_utc": week_start,
        "week_end_utc": week_end,
        "evaluation_days": days,
        "initial_nav": config.initial_nav,
        "final_nav": final_nav,
        "net_return": final_nav / config.initial_nav - 1.0,
        "daily_log_growth": log(final_nav / config.initial_nav) / days,
        "daily_geometric_growth": daily_growth,
        "target_daily_geometric_growth": config.minimum_daily_geometric_growth,
        "target_met": daily_growth >= config.minimum_daily_geometric_growth,
        "trades": len(trades),
        "trades_per_day": len(trades) / days,
        "wins": len(positive),
        "losses": len(non_positive),
        "win_rate": win_rate,
        "mean_net_r": mean_r,
        "median_net_r": median(values) if values else 0.0,
        "profit_factor_r": gross_profit / gross_loss if gross_loss > 0 else (inf if gross_profit > 0 else 0.0),
        "largest_realized_loss_r": min(values) if values else 0.0,
        "largest_realized_win_r": max(values) if values else 0.0,
        "realized_loss_budget_breaches": sum(value < -1.01 for value in values),
        "max_drawdown": float(replay["max_drawdown"]),
        "closed_nav_max_drawdown": float(replay["closed_nav_max_drawdown"]),
        "minimum_mark_to_market_nav": float(replay["minimum_mark_to_market_nav"]),
        "positive_days": sum(row["return"] > 0 for row in daily_rows),
        "negative_days": sum(row["return"] < 0 for row in daily_rows),
        "flat_days": sum(row["return"] == 0 for row in daily_rows),
        "daily_returns": daily_rows,
        "directions": {
            direction: sum(trade["direction"] == direction for trade in trades)
            for direction in ("LONG", "SHORT")
        },
        "scenario_kinds": {
            kind: sum(trade["scenario_kind"] == kind for trade in trades)
            for kind in ("LCPT", "TPR")
        },
        "exit_reasons": {
            reason: sum(trade["exit_reason"] == reason for trade in trades)
            for reason in ("TARGET", "STOP", "TRAIL", "TIME", "END_OF_RUN")
        },
        "largest_winner_share_of_positive_r": positive_sorted[0] / sum(positive_sorted) if positive_sorted else 0.0,
        "top_three_winner_share_of_positive_r": sum(positive_sorted[:3]) / sum(positive_sorted) if positive_sorted else 0.0,
        "risk_fraction": config.risk_fraction,
        "single_slot_enforced": bool(replay["single_slot_enforced"]),
        "gate_checks": gate_checks,
        "gate_passed": all(gate_checks.values()),
        "cost_assumptions": {
            "taker_fee_bps_each_fill": config.taker_fee_bps,
            "slippage_and_market_impact_bps_each_fill": config.slippage_impact_bps,
            "funding_bps_per_8h": config.funding_bps_per_8h,
        },
        "scenario_counters": {
            "signals": int(replay["signals"]),
            "blocked_signals": int(replay["blocked_signals"]),
            "invalidated_before_entry": int(replay["invalidated_before_entry"]),
            "protection_activations": int(replay["protection_activations"]),
            "structural_stop_updates": int(replay["structural_stop_updates"]),
        },
        "data_stats": data_stats,
        "trades_detail": trades,
    }
