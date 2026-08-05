"""After-cost NAV diagnostics for the FAR candidate."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from math import inf, log
from statistics import median
from typing import Any

from far_model import Direction, ExitReason, FarConfig, FarTrade

NS_PER_DAY = 86_400_000_000_000


def _date_key(timestamp_ns: int) -> str:
    return datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc).date().isoformat()


def build_metrics(
    config: FarConfig,
    nav: float,
    max_drawdown: float,
    trades: list[FarTrade],
    counters: dict[str, int],
    start_ns: int,
    end_ns: int,
) -> dict[str, Any]:
    evaluation_days = (end_ns - start_ns) / NS_PER_DAY
    if evaluation_days <= 0:
        raise ValueError("invalid evaluation interval")
    positive = [trade for trade in trades if trade.net_pnl > 0]
    non_positive = [trade for trade in trades if trade.net_pnl <= 0]
    gross_profit_r = sum(trade.net_r for trade in positive)
    gross_loss_r = abs(sum(trade.net_r for trade in non_positive))
    daily_nav: dict[str, float] = {}
    for trade in trades:
        daily_nav[_date_key(trade.exit_time_ns)] = trade.nav_after
    cursor = datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc).date()
    final_day = datetime.fromtimestamp(end_ns / 1e9, tz=timezone.utc).date()
    running_nav = config.initial_nav
    daily_returns: list[dict[str, Any]] = []
    while cursor < final_day:
        next_nav = daily_nav.get(cursor.isoformat(), running_nav)
        daily_returns.append(
            {"date": cursor.isoformat(), "nav": next_nav, "return": next_nav / running_nav - 1.0}
        )
        running_nav = next_nav
        cursor = date.fromordinal(cursor.toordinal() + 1)
    daily_growth = (nav / config.initial_nav) ** (1.0 / evaluation_days) - 1.0
    directions = {
        direction.value: sum(trade.direction is direction for trade in trades)
        for direction in Direction
    }
    exit_reasons = {
        reason.value: sum(trade.exit_reason is reason for trade in trades)
        for reason in ExitReason
    }
    positive_r = sorted((trade.net_r for trade in positive), reverse=True)
    return {
        "candidate": config.candidate,
        "instrument_id": config.instrument_id,
        "initial_nav": config.initial_nav,
        "final_nav": nav,
        "net_return": nav / config.initial_nav - 1.0,
        "evaluation_days": evaluation_days,
        "daily_log_growth": log(nav / config.initial_nav) / evaluation_days,
        "daily_geometric_growth": daily_growth,
        "target_daily_geometric_growth": 0.01,
        "target_met": daily_growth >= 0.01,
        "trades": len(trades),
        "trades_per_day": len(trades) / evaluation_days,
        "wins": len(positive),
        "losses": len(non_positive),
        "win_rate": len(positive) / len(trades) if trades else 0.0,
        "mean_net_r": sum(trade.net_r for trade in trades) / len(trades) if trades else 0.0,
        "median_net_r": median([trade.net_r for trade in trades]) if trades else 0.0,
        "profit_factor_r": (
            gross_profit_r / gross_loss_r
            if gross_loss_r > 0
            else (inf if gross_profit_r > 0 else 0.0)
        ),
        "max_drawdown": max_drawdown,
        "positive_days": sum(row["return"] > 0 for row in daily_returns),
        "negative_days": sum(row["return"] < 0 for row in daily_returns),
        "daily_returns": daily_returns,
        "directions": directions,
        "exit_reasons": exit_reasons,
        **counters,
        "single_slot_enforced": True,
        "risk_fraction": config.risk_fraction,
        "largest_winner_share_of_positive_r": (
            positive_r[0] / sum(positive_r) if positive_r else 0.0
        ),
        "top_three_winner_share_of_positive_r": (
            sum(positive_r[:3]) / sum(positive_r) if positive_r else 0.0
        ),
        "cost_assumptions": {
            "taker_fee_bps_each_fill": config.taker_fee_bps,
            "slippage_and_market_impact_bps_each_fill": config.slippage_impact_bps,
            "funding_bps_per_8h": config.funding_bps_per_8h,
        },
        "trades_detail": [asdict(trade) for trade in trades],
    }
