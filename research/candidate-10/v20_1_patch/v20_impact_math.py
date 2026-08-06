"""Pure causal execution-cost mathematics for candidate 10 v20.1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import exp, log, sqrt
from typing import Any


@dataclass(frozen=True, slots=True)
class RiskSizeSolution:
    quantity: float
    impact_per_side: float
    participation: float
    per_unit_loss: float


def solve_risk_quantity(
    *,
    risk_budget: float,
    entry: float,
    stop: float,
    taker_fee: float,
    base_impact: float,
    atr: float,
    liquidity_notional: float,
    iterations: int = 32,
) -> RiskSizeSolution | None:
    """Solve quantity and square-root impact together without a nominal cap."""
    if min(risk_budget, entry, stop, atr, liquidity_notional) <= 0.0:
        return None
    if taker_fee < 0.0 or base_impact < 0.0:
        return None
    distance = abs(entry - stop)
    fee_loss = entry * taker_fee + stop * taker_fee
    if distance <= 0.0:
        return None
    quantity = risk_budget / max(distance + fee_loss + 2.0 * base_impact, 1e-12)
    for _ in range(max(1, iterations)):
        participation = max(0.0, quantity * entry / liquidity_notional)
        impact = max(base_impact, atr * sqrt(participation))
        per_unit_loss = distance + fee_loss + 2.0 * impact
        updated = risk_budget / max(per_unit_loss, 1e-12)
        if abs(updated - quantity) <= max(1e-12, abs(quantity) * 1e-10):
            quantity = updated
            break
        quantity = updated
    participation = max(0.0, quantity * entry / liquidity_notional)
    impact = max(base_impact, atr * sqrt(participation))
    per_unit_loss = distance + fee_loss + 2.0 * impact
    return RiskSizeSolution(quantity, impact, participation, per_unit_loss)


def geometric_daily_metrics(starting_nav: float, daily_nav: dict[str, float]) -> dict[str, Any]:
    previous = starting_nav
    returns: list[float] = []
    peak = starting_nav
    max_drawdown = 0.0
    for day in sorted(daily_nav):
        value = float(daily_nav[day])
        returns.append(value / previous - 1.0 if previous > 0.0 else -1.0)
        previous = value
        peak = max(peak, value)
        if peak > 0.0:
            max_drawdown = max(max_drawdown, 1.0 - value / peak)
    growth = exp(sum(log(max(1e-15, 1.0 + item)) for item in returns) / len(returns)) - 1.0 if returns else 0.0
    return {
        "daily_returns": returns,
        "geometric_daily_growth": growth,
        "daily_max_drawdown": max_drawdown,
        "positive_days": sum(item > 0.0 for item in returns),
        "negative_days": sum(item < 0.0 for item in returns),
        "flat_days": sum(item == 0.0 for item in returns),
    }


def impact_adjusted_ledger(
    *,
    starting_nav: float,
    ending_nav: float,
    daily_nav: dict[str, float],
    equity_curve: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    tick_max_drawdown: float,
) -> dict[str, Any]:
    """Debit causal expected impact from the otherwise engine-accounted NAV."""
    costs: list[tuple[int, float]] = []
    by_day: dict[str, float] = {}
    total = 0.0
    for trade in trades:
        cost = max(0.0, float(trade.get("conservative_impact_cost", 0.0) or 0.0))
        ts_ns = int(float(trade.get("closed_ts_ns", 0) or 0))
        if cost <= 0.0 or ts_ns <= 0:
            continue
        total += cost
        costs.append((ts_ns, cost))
        day = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).date().isoformat()
        by_day[day] = by_day.get(day, 0.0) + cost
    costs.sort()

    cumulative = 0.0
    adjusted_daily: dict[str, float] = {}
    for day in sorted(daily_nav):
        cumulative += by_day.get(day, 0.0)
        adjusted_daily[day] = float(daily_nav[day]) - cumulative
    daily = geometric_daily_metrics(starting_nav, adjusted_daily)

    event_index = 0
    cumulative_cost = 0.0
    peak = starting_nav
    curve_mdd = 0.0
    adjusted_curve: list[dict[str, float | int]] = []
    for point in sorted(equity_curve, key=lambda row: int(float(row["ts_ns"]))):
        ts_ns = int(float(point["ts_ns"]))
        while event_index < len(costs) and costs[event_index][0] <= ts_ns:
            cumulative_cost += costs[event_index][1]
            event_index += 1
        equity = float(point["equity"]) - cumulative_cost
        peak = max(peak, equity)
        if peak > 0.0:
            curve_mdd = max(curve_mdd, 1.0 - equity / peak)
        adjusted_curve.append({"ts_ns": ts_ns, "equity": equity})

    adjusted_ending = ending_nav - total
    return {
        "impact_adjustment_total": total,
        "impact_adjusted_ending_nav": adjusted_ending,
        "impact_adjusted_net_return": adjusted_ending / starting_nav - 1.0,
        "impact_adjusted_intraday_max_drawdown": max(tick_max_drawdown, curve_mdd),
        "impact_adjusted_daily_nav": [
            {"date": day, "nav": adjusted_daily[day]} for day in sorted(adjusted_daily)
        ],
        "impact_adjusted_equity_curve": adjusted_curve,
        "impact_adjusted_geometric_daily_growth": daily["geometric_daily_growth"],
        "impact_adjusted_daily_max_drawdown": daily["daily_max_drawdown"],
        "impact_adjusted_positive_days": daily["positive_days"],
        "impact_adjusted_negative_days": daily["negative_days"],
        "impact_adjusted_flat_days": daily["flat_days"],
    }
