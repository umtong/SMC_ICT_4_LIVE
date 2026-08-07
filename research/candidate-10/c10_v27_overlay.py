"""Realistic all-cost overlay for Candidate 11 market-leadership SCDAM.

The market logic, entry, stop, target, session framing, and global mutex remain
unchanged.  This module only makes the existing three-percent NAV risk budget
self-consistent with size-dependent execution impact and audits cost-after PnL.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_DOWN
from math import sqrt
import os
from typing import Any

from logic import SizeDecision


@dataclass(frozen=True, slots=True)
class ImpactSolution:
    quantity: Decimal
    impact_per_side: Decimal
    participation: Decimal
    per_unit_loss: Decimal
    planned_loss_budget: Decimal
    expected_total_loss: Decimal
    required_margin: Decimal
    liquidity_notional: Decimal
    atr: Decimal


@dataclass(slots=True)
class LiveImpactLedger:
    cumulative_cost: Decimal = Decimal("0")

    def conservative_equity(self, engine_equity: Decimal) -> Decimal:
        return engine_equity - self.cumulative_cost

    def debit(self, *, quantity: Decimal, impact_per_unit: Decimal) -> Decimal:
        if quantity < 0 or impact_per_unit < 0:
            raise ValueError("impact debit inputs must be nonnegative")
        cost = quantity * impact_per_unit
        self.cumulative_cost += cost
        return cost


class CostAwareRiskSizer:
    """Fixed-point risk sizing with causal size-dependent impact."""

    def __init__(self, risk_fraction: float = 0.03) -> None:
        if not 0 < risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be in (0, 0.03]")
        self.risk_fraction = Decimal(str(risk_fraction))
        self._atr: Decimal | None = None
        self._liquidity_notional: Decimal | None = None
        self._base_impact: Decimal | None = None
        self.last_solution: ImpactSolution | None = None

    @staticmethod
    def _floor(value: Decimal, increment: Decimal) -> Decimal:
        return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment

    def set_context(
        self,
        *,
        atr: float,
        liquidity_notional: float,
        tick_size: float,
    ) -> None:
        atr_d = Decimal(str(atr))
        liquidity_d = Decimal(str(liquidity_notional))
        tick_d = Decimal(str(tick_size))
        if atr_d <= 0 or liquidity_d <= 0 or tick_d <= 0:
            raise ValueError("cost context must be positive")
        self._atr = atr_d
        self._liquidity_notional = liquidity_d
        self._base_impact = max(tick_d * Decimal("2"), atr_d * Decimal("0.01"))

    def size(
        self,
        *,
        nav: Decimal,
        loss_per_unit: Decimal,
        entry_price: Decimal,
        quantity_increment: Decimal,
        min_quantity: Decimal,
        min_notional: Decimal,
        margin_init: Decimal,
        free_balance: Decimal,
    ) -> SizeDecision:
        if nav <= 0 or loss_per_unit <= 0 or entry_price <= 0:
            raise ValueError("NAV, loss and entry must be positive")
        if self._atr is None or self._liquidity_notional is None or self._base_impact is None:
            raise RuntimeError("size-dependent impact context was not set")

        budget = nav * self.risk_fraction
        quantity = budget / max(loss_per_unit + Decimal("2") * self._base_impact, Decimal("1e-18"))
        impact = self._base_impact
        participation = Decimal("0")
        per_unit = loss_per_unit + Decimal("2") * impact
        for _ in range(64):
            participation = max(
                Decimal("0"),
                quantity * entry_price / self._liquidity_notional,
            )
            impact = max(
                self._base_impact,
                self._atr * Decimal(str(sqrt(float(participation)))),
            )
            per_unit = loss_per_unit + Decimal("2") * impact
            updated = budget / max(per_unit, Decimal("1e-18"))
            if abs(updated - quantity) <= max(Decimal("1e-12"), abs(quantity) * Decimal("1e-10")):
                quantity = updated
                break
            quantity = updated

        quantity = self._floor(quantity, quantity_increment)
        notional = quantity * entry_price
        expected = quantity * per_unit
        margin = notional * max(margin_init, Decimal("0"))
        feasible = True
        reason = "OK"
        if quantity < min_quantity:
            feasible, reason = False, "BELOW_MIN_QUANTITY"
        elif notional < min_notional:
            feasible, reason = False, "BELOW_MIN_NOTIONAL"
        elif margin > free_balance:
            feasible, reason = False, "ACTUAL_MARGIN_INFEASIBLE"
        elif expected > budget + max(Decimal("0.01"), budget * Decimal("1e-9")):
            feasible, reason = False, "COST_AFTER_RISK_BUDGET_EXCEEDED"

        self.last_solution = ImpactSolution(
            quantity=quantity if feasible else Decimal("0"),
            impact_per_side=impact,
            participation=participation,
            per_unit_loss=per_unit,
            planned_loss_budget=budget,
            expected_total_loss=expected if feasible else Decimal("0"),
            required_margin=margin,
            liquidity_notional=self._liquidity_notional,
            atr=self._atr,
        )
        return SizeDecision(
            quantity=quantity if feasible else Decimal("0"),
            planned_loss_budget=budget,
            expected_loss_per_unit=per_unit,
            expected_total_loss=expected if feasible else Decimal("0"),
            required_margin=margin,
            feasible=feasible,
            reason=reason,
        )


class LeadershipGateAdapter:
    def __init__(self, original: Any, *, ablated: bool) -> None:
        self.original = original
        self.ablated = bool(ablated)

    def observe_batch(self, *args: Any, **kwargs: Any) -> Any:
        return self.original.observe_batch(*args, **kwargs)

    def decide(self, *args: Any, **kwargs: Any) -> Any:
        decision = self.original.decide(*args, **kwargs)
        if not self.ablated:
            return decision
        return replace(
            decision,
            approved=True,
            reason="ABLATION_MARKET_LEADERSHIP_REMOVED",
        )


def build_leadership_gate(
    gate_type: type,
    symbols: tuple[str, ...],
    *,
    lookback_bars: int,
) -> LeadershipGateAdapter:
    return LeadershipGateAdapter(
        gate_type(symbols, lookback_bars=lookback_bars),
        ablated=os.environ.get("C10_V27_ABLATE_LEADERSHIP", "0") == "1",
    )


def _pnl_values(positions: Any) -> list[float]:
    if positions is None or getattr(positions, "empty", True):
        return []
    column = next((name for name in ("realized_pnl", "pnl") if name in positions.columns), None)
    if column is None:
        return []
    values: list[float] = []
    for raw in positions[column].tolist():
        text = str(raw).strip()
        values.append(float(text.split()[0]))
    return values


def apply_cost_overlay(
    *,
    metrics: dict[str, Any],
    positions: Any,
    cost_records: list[dict[str, Any]],
    starting_nav: float,
    evaluation_days: int,
) -> dict[str, Any]:
    filled = sorted(
        (row for row in cost_records if float(row.get("entry_filled_qty", 0.0)) > 0.0),
        key=lambda row: int(row.get("first_entry_fill_ts_ns", 0)),
    )
    pnls = _pnl_values(positions)
    mapping_error = len(filled) != len(pnls)
    count = min(len(filled), len(pnls))
    adjusted_pnls: list[float] = []
    total_impact = 0.0
    for index in range(count):
        row = filled[index]
        impact = float(row.get("entry_impact_cost", 0.0)) + float(row.get("exit_impact_cost", 0.0))
        total_impact += impact
        row["engine_realized_pnl"] = pnls[index]
        row["impact_adjusted_pnl"] = pnls[index] - impact
        adjusted_pnls.append(pnls[index] - impact)

    total_impact = max(
        total_impact,
        sum(
            float(row.get("entry_impact_cost", 0.0)) + float(row.get("exit_impact_cost", 0.0))
            for row in cost_records
        ),
    )
    adjusted_ending = float(metrics["final_nav"]) - total_impact
    growth = (
        (adjusted_ending / starting_nav) ** (1.0 / evaluation_days) - 1.0
        if adjusted_ending > 0.0 and evaluation_days > 0
        else -1.0
    )
    wins = [value for value in adjusted_pnls if value > 0.0]
    losses = [value for value in adjusted_pnls if value < 0.0]
    payoff = (
        (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
        if wins and losses
        else (float("inf") if wins else None)
    )
    peak = starting_nav
    equity = starting_nav
    max_dd = 0.0
    for value in adjusted_pnls:
        equity += value
        peak = max(peak, equity)
        if peak > 0.0:
            max_dd = max(max_dd, 1.0 - equity / peak)
    concentration = max(wins) / sum(wins) if wins and sum(wins) > 0 else 0.0

    risk_violations = []
    for row in cost_records:
        budget = float(row.get("planned_loss_budget", 0.0))
        expected = float(row.get("expected_total_loss", 0.0))
        if expected > budget + max(0.01, abs(budget) * 1e-9):
            risk_violations.append(
                {
                    "scenario_id": row.get("scenario_id"),
                    "planned_loss_budget": budget,
                    "expected_total_loss": expected,
                },
            )

    metrics.update(
        {
            "candidate_generation": "candidate-10-v27-costed-market-leadership-scdam",
            "variant": (
                "ablation-market-leadership-removed"
                if os.environ.get("C10_V27_ABLATE_LEADERSHIP", "0") == "1"
                else "full-market-leadership"
            ),
            "impact_model": (
                "max(2 ticks, 0.01 ATR, ATR*sqrt(order_notional/"
                "causal_prior_120m_median_1m_notional)) per side"
            ),
            "impact_cost_total": total_impact,
            "impact_adjusted_ending_nav": adjusted_ending,
            "impact_adjusted_net_return": adjusted_ending / starting_nav - 1.0,
            "impact_adjusted_geometric_daily_growth": growth,
            "impact_adjusted_closed_trade_max_drawdown": max_dd,
            "impact_adjusted_wins": len(wins),
            "impact_adjusted_losses": len(losses),
            "impact_adjusted_win_rate": len(wins) / len(adjusted_pnls) if adjusted_pnls else 0.0,
            "impact_adjusted_payoff_ratio": None if payoff in (None, float("inf")) else payoff,
            "impact_adjusted_all_trades_won": bool(wins) and not losses,
            "impact_adjusted_largest_win_concentration": concentration,
            "cost_record_count": len(cost_records),
            "filled_cost_record_count": len(filled),
            "position_cost_mapping_error": mapping_error,
            "risk_budget_violation_count": len(risk_violations),
            "risk_budget_violations": risk_violations,
            "cost_records": cost_records,
        },
    )
    metrics["target_pass"] = bool(
        not mapping_error
        and not risk_violations
        and not metrics.get("engine_errors")
        and int(metrics.get("global_slot_overlap_count", 0)) == 0
        and len(adjusted_pnls) >= 3
        and len(wins) >= 2
        and metrics["impact_adjusted_win_rate"] >= 0.65
        and growth >= 0.01
        and max_dd <= 0.20
        and concentration <= 0.67
    )
    metrics["success_claim"] = False
    return metrics
