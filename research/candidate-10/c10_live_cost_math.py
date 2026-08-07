"""Pure causal execution-cost mathematics for candidate 10 v20.1+."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import exp, isfinite, log, sqrt
from typing import Any


@dataclass(frozen=True, slots=True)
class RiskSizeSolution:
    quantity: float
    impact_per_side: float
    participation: float
    per_unit_loss: float


@dataclass(slots=True)
class LiveImpactLedger:
    """Side ledger for modeled impact which Nautilus account cash omits.

    The engine remains authoritative for fills, commissions, positions and raw
    account NAV. Expected market impact is an explicitly declared additional
    cost, so it is debited here at the actual fill timestamp and subtracted from
    engine equity before every later risk budget is computed.
    """

    cumulative_cost: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)

    def conservative_equity(self, engine_equity: float) -> float:
        value = float(engine_equity) - self.cumulative_cost
        return value if isfinite(value) else float("-inf")

    def debit(
        self,
        *,
        quantity: float,
        impact_per_unit: float,
        ts_ns: int,
        role: str,
        scenario_id: str,
    ) -> float:
        quantity = float(quantity)
        impact_per_unit = float(impact_per_unit)
        if quantity < 0.0 or impact_per_unit < 0.0:
            raise ValueError("modeled impact inputs must be nonnegative")
        cost = quantity * impact_per_unit
        if not isfinite(cost):
            raise ValueError("modeled impact cost must be finite")
        self.cumulative_cost += cost
        self.events.append(
            {
                "ts_ns": int(ts_ns),
                "role": str(role),
                "scenario_id": str(scenario_id),
                "quantity": quantity,
                "impact_per_unit": impact_per_unit,
                "cost": cost,
                "cumulative_cost": self.cumulative_cost,
            },
        )
        return cost


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


def geometric_daily_metrics(
    starting_nav: float,
    daily_nav: dict[str, float],
) -> dict[str, Any]:
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
    growth = (
        exp(
            sum(log(max(1e-15, 1.0 + item)) for item in returns)
            / len(returns),
        )
        - 1.0
        if returns
        else 0.0
    )
    return {
        "daily_returns": returns,
        "geometric_daily_growth": growth,
        "daily_max_drawdown": max_drawdown,
        "positive_days": sum(item > 0.0 for item in returns),
        "negative_days": sum(item < 0.0 for item in returns),
        "flat_days": sum(item == 0.0 for item in returns),
    }


def _trade_impact_events(trades: list[dict[str, Any]]) -> list[tuple[int, float]]:
    """Return modeled-impact debits at actual entry and exit timestamps.

    New records carry split entry/exit costs. The legacy close-time total is
    retained only as a backwards-compatible fallback for older evidence.
    """

    events: list[tuple[int, float]] = []
    for trade in trades:
        has_split = (
            "conservative_entry_impact_cost" in trade
            or "conservative_exit_impact_cost" in trade
        )
        if has_split:
            entry_cost = max(
                0.0,
                float(trade.get("conservative_entry_impact_cost", 0.0) or 0.0),
            )
            exit_cost = max(
                0.0,
                float(trade.get("conservative_exit_impact_cost", 0.0) or 0.0),
            )
            entry_ts = int(
                float(
                    trade.get(
                        "opened_ts_ns",
                        trade.get("entry_submit_ts_ns", 0),
                    )
                    or 0,
                ),
            )
            exit_ts = int(float(trade.get("closed_ts_ns", 0) or 0))
            if entry_cost > 0.0 and entry_ts > 0:
                events.append((entry_ts, entry_cost))
            if exit_cost > 0.0 and exit_ts > 0:
                events.append((exit_ts, exit_cost))
            continue

        cost = max(
            0.0,
            float(trade.get("conservative_impact_cost", 0.0) or 0.0),
        )
        ts_ns = int(float(trade.get("closed_ts_ns", 0) or 0))
        if cost > 0.0 and ts_ns > 0:
            events.append((ts_ns, cost))
    events.sort()
    return events


def live_ledger_diagnostics(
    *,
    trades: list[dict[str, Any]],
    risk_fraction: float,
    adjusted_ending_nav: float,
) -> dict[str, Any]:
    """Audit that every risk budget used current all-cost account NAV."""

    violations: list[dict[str, Any]] = []
    max_error = 0.0
    cumulative_impact = 0.0
    ordered = sorted(
        trades,
        key=lambda row: int(float(row.get("opened_ts_ns", 0) or 0)),
    )
    for trade in ordered:
        scenario_id = str(trade.get("scenario_id", ""))
        engine_start = float(trade.get("start_equity", 0.0) or 0.0)
        ledger_before = float(
            trade.get("impact_ledger_cost_before_entry", 0.0) or 0.0,
        )
        recorded_basis = float(
            trade.get(
                "planned_loss_budget_nav_basis",
                trade.get("conservative_start_equity", 0.0),
            )
            or 0.0,
        )
        recorded_conservative_start = float(
            trade.get("conservative_start_equity", recorded_basis)
            or recorded_basis,
        )
        expected_basis = engine_start - cumulative_impact
        budget = float(trade.get("planned_loss_budget", 0.0) or 0.0)
        expected_budget = expected_basis * risk_fraction
        planned_loss = float(trade.get("planned_loss", 0.0) or 0.0)

        ledger_before_error = abs(ledger_before - cumulative_impact)
        basis_error = abs(recorded_basis - expected_basis)
        start_error = abs(recorded_conservative_start - expected_basis)
        budget_error = abs(budget - expected_budget)
        max_error = max(
            max_error,
            ledger_before_error,
            basis_error,
            start_error,
            budget_error,
        )
        tolerance = max(0.01, abs(expected_budget) * 1e-9)

        trade_impact = max(
            0.0,
            float(trade.get("conservative_impact_cost", 0.0) or 0.0),
        )
        cumulative_impact += trade_impact
        engine_end = float(trade.get("end_equity", 0.0) or 0.0)
        recorded_conservative_end = float(
            trade.get("conservative_end_equity", engine_end - cumulative_impact)
            or 0.0,
        )
        expected_conservative_end = engine_end - cumulative_impact
        end_error = abs(recorded_conservative_end - expected_conservative_end)
        max_error = max(max_error, end_error)

        if (
            ledger_before_error > tolerance
            or basis_error > tolerance
            or start_error > tolerance
            or budget_error > tolerance
            or planned_loss > budget + tolerance
            or end_error > tolerance
        ):
            violations.append(
                {
                    "scenario_id": scenario_id,
                    "engine_start_equity": engine_start,
                    "prior_modeled_impact_expected": (
                        cumulative_impact - trade_impact
                    ),
                    "prior_modeled_impact_recorded": ledger_before,
                    "nav_basis_recorded": recorded_basis,
                    "nav_basis_expected": expected_basis,
                    "conservative_start_recorded": (
                        recorded_conservative_start
                    ),
                    "budget": budget,
                    "expected_budget": expected_budget,
                    "planned_loss": planned_loss,
                    "conservative_end_recorded": (
                        recorded_conservative_end
                    ),
                    "conservative_end_expected": (
                        expected_conservative_end
                    ),
                    "tolerance": tolerance,
                },
            )

    total = cumulative_impact
    last_recorded = (
        float(ordered[-1].get("conservative_end_equity", adjusted_ending_nav))
        if ordered
        else adjusted_ending_nav
    )
    ending_error = abs(last_recorded - adjusted_ending_nav)
    ending_tolerance = max(0.01, abs(adjusted_ending_nav) * 1e-9)
    return {
        "modeled_impact_cost_total": total,
        "risk_budget_violation_count": len(violations),
        "risk_budget_violations": violations[:50],
        "max_ledger_or_budget_error": max_error,
        "recorded_vs_reported_ending_nav_error": ending_error,
        "recorded_vs_reported_ending_nav_match": (
            ending_error <= ending_tolerance
        ),
        "sizing_basis": (
            "CURRENT_NAUTILUS_WHOLE_ACCOUNT_NAV_MINUS_ALL_PRIOR_"
            "MODELED_IMPACT_DEBITS"
        ),
        "impact_debit_timing": "ACTUAL_ENTRY_AND_EXIT_FILL_TIMESTAMPS",
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
    costs = _trade_impact_events(trades)
    by_day: dict[str, float] = {}
    total = 0.0
    for ts_ns, cost in costs:
        total += cost
        day = datetime.fromtimestamp(
            ts_ns / 1_000_000_000,
            tz=timezone.utc,
        ).date().isoformat()
        by_day[day] = by_day.get(day, 0.0) + cost

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
    for point in sorted(
        equity_curve,
        key=lambda row: int(float(row["ts_ns"])),
    ):
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
        "impact_adjusted_intraday_max_drawdown": max(
            tick_max_drawdown,
            curve_mdd,
        ),
        "impact_adjusted_daily_nav": [
            {"date": day, "nav": adjusted_daily[day]}
            for day in sorted(adjusted_daily)
        ],
        "impact_adjusted_equity_curve": adjusted_curve,
        "impact_adjusted_geometric_daily_growth": daily[
            "geometric_daily_growth"
        ],
        "impact_adjusted_daily_max_drawdown": daily["daily_max_drawdown"],
        "impact_adjusted_positive_days": daily["positive_days"],
        "impact_adjusted_negative_days": daily["negative_days"],
        "impact_adjusted_flat_days": daily["flat_days"],
        "impact_debit_event_count": len(costs),
        "impact_debit_timing": "ACTUAL_ENTRY_AND_EXIT_FILL_TIMESTAMPS",
    }
