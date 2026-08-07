"""Live conservative impact ledger for candidate-10 liquidation research.

NautilusTrader remains authoritative for fills, commissions, positions and raw
account NAV. The declared size-dependent market impact is an additional cost
not posted to the engine account, so this overlay debits it in a side ledger at
actual entry and exit fill timestamps. Every later 3% risk budget is based on
engine whole-account NAV minus all prior modeled impact debits.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import c10_liquidation_research as _research
from c10_live_cost_math import (
    LiveImpactLedger,
    impact_adjusted_ledger,
    live_ledger_diagnostics,
)


_BASE_STRATEGY = _research.LiquidationCandidate10Strategy
_BASE_RUN = _research.run_liquidation_backtest


class LiveCostLiquidationStrategy(_BASE_STRATEGY):
    """Debit modeled impact live without changing signals or order routing."""

    def __init__(self, config: Any):
        super().__init__(config)
        starting = float(config.starting_balance)
        self.live_impact_ledger = LiveImpactLedger()
        self._cost_after_sizing_context = False
        self.conservative_last_equity = starting
        self.conservative_max_equity = starting
        self.conservative_max_drawdown = 0.0

    def _engine_equity(self) -> float:
        return super()._equity()

    def _conservative_equity(self) -> float:
        return self.live_impact_ledger.conservative_equity(
            self._engine_equity(),
        )

    def _equity(self) -> float:
        # The parent fixed-point solver calls _equity exactly for the risk NAV.
        # Restrict the substitution to that call; every engine report and raw
        # PnL calculation continues to use Nautilus account equity.
        engine = self._engine_equity()
        if getattr(self, "_cost_after_sizing_context", False):
            return self.live_impact_ledger.conservative_equity(engine)
        return engine

    def _quantity_for_plan(self, plan: Any, entry: float):
        conservative_nav = self._conservative_equity()
        self._cost_after_sizing_context = True
        try:
            result = super()._quantity_for_plan(plan, entry)
        finally:
            self._cost_after_sizing_context = False
        if result is None:
            return None
        quantity, execution_plan = result
        enriched = dict(execution_plan)
        enriched["risk_budget_nav"] = conservative_nav
        return quantity, enriched

    def _submit_entry(self, plan: Any, tick: Any) -> bool:
        conservative_start = self._conservative_equity()
        ledger_before = self.live_impact_ledger.cumulative_cost
        submitted = super()._submit_entry(plan, tick)
        if not submitted or self.active_trade is None:
            return submitted
        self.active_trade["conservative_start_equity"] = conservative_start
        self.active_trade["planned_loss_budget_nav_basis"] = (
            conservative_start
        )
        self.active_trade["impact_ledger_cost_before_entry"] = ledger_before
        self.active_trade["conservative_entry_impact_cost"] = 0.0
        self.active_trade["conservative_exit_impact_cost"] = 0.0
        self._append_execution_event(
            event_type="COST_LEDGER_SNAPSHOT",
            reason_code="CURRENT_ALL_COST_NAV_FIXED_BEFORE_ENTRY",
            ts_ns=int(tick.ts_event),
            previous_state="ORDER_PENDING",
            next_state="ORDER_PENDING",
            reference_price=tick.price.as_double(),
            details={
                "engine_equity": self._engine_equity(),
                "prior_modeled_impact": ledger_before,
                "conservative_nav": conservative_start,
                "risk_fraction": str(self.config.risk_fraction),
                "planned_loss_budget": self.active_trade[
                    "planned_loss_budget"
                ],
            },
        )
        return submitted

    def _observe_live_equity(self) -> None:
        conservative = self._conservative_equity()
        self.conservative_last_equity = conservative
        self.conservative_max_equity = max(
            self.conservative_max_equity,
            conservative,
        )
        if self.conservative_max_equity > 0.0:
            self.conservative_max_drawdown = max(
                self.conservative_max_drawdown,
                1.0 - conservative / self.conservative_max_equity,
            )

    def _debit_active_trade_impact(
        self,
        *,
        role: str,
        quantity: float,
        ts_ns: int,
    ) -> float:
        if self.active_trade is None or quantity <= 0.0:
            return 0.0
        if role == "ENTRY":
            impact_key = "expected_entry_impact"
            cost_key = "conservative_entry_impact_cost"
            ts_key = "entry_impact_debit_ts_ns"
        elif role == "EXIT":
            impact_key = "expected_exit_impact"
            cost_key = "conservative_exit_impact_cost"
            ts_key = "exit_impact_debit_ts_ns"
        else:
            raise ValueError(f"unknown impact debit role: {role}")
        impact = max(0.0, float(self.active_trade.get(impact_key, 0.0)))
        cost = self.live_impact_ledger.debit(
            quantity=quantity,
            impact_per_unit=impact,
            ts_ns=ts_ns,
            role=role,
            scenario_id=str(self.active_trade["scenario_id"]),
        )
        self.active_trade[cost_key] = (
            float(self.active_trade.get(cost_key, 0.0)) + cost
        )
        if role == "ENTRY":
            self.active_trade.setdefault(ts_key, int(ts_ns))
        else:
            self.active_trade[ts_key] = int(ts_ns)
        self.active_trade["impact_ledger_cost_after_last_debit"] = (
            self.live_impact_ledger.cumulative_cost
        )
        self._append_execution_event(
            event_type="MODELED_IMPACT_DEBITED",
            reason_code=f"{role}_FILL_SIZE_DEPENDENT_IMPACT",
            ts_ns=ts_ns,
            previous_state=str(
                self.active_trade.get("event_state", "ORDER_PENDING"),
            ),
            next_state=str(
                self.active_trade.get("event_state", "ORDER_PENDING"),
            ),
            details={
                "role": role,
                "quantity": quantity,
                "impact_per_unit": impact,
                "cost": cost,
                "cumulative_modeled_impact": (
                    self.live_impact_ledger.cumulative_cost
                ),
                "conservative_nav_after_debit": (
                    self._conservative_equity()
                ),
            },
        )
        self._observe_live_equity()
        return cost

    def on_order_filled(self, event: Any) -> None:
        if self.active_trade is None:
            return
        client_order_id = getattr(event, "client_order_id", None)
        is_entry = client_order_id == self.entry_order_client_id
        super().on_order_filled(event)
        ts_ns = int(getattr(event, "ts_event", self.clock.timestamp_ns()))
        quantity = event.last_qty.as_double()
        if is_entry:
            self._debit_active_trade_impact(
                role="ENTRY",
                quantity=quantity,
                ts_ns=ts_ns,
            )
        elif self.exit_pending:
            self._debit_active_trade_impact(
                role="EXIT",
                quantity=quantity,
                ts_ns=ts_ns,
            )

    def _debit_missing_fill_impact_before_close(self, ts_ns: int) -> None:
        if self.active_trade is None:
            return
        quantity = float(
            self.active_trade.get(
                "actual_entry_qty",
                self.active_trade.get("quantity", 0.0),
            ),
        )
        for role, impact_key, cost_key, fallback_ts in (
            (
                "ENTRY",
                "expected_entry_impact",
                "conservative_entry_impact_cost",
                int(self.active_trade.get("opened_ts_ns", ts_ns)),
            ),
            (
                "EXIT",
                "expected_exit_impact",
                "conservative_exit_impact_cost",
                ts_ns,
            ),
        ):
            impact = max(0.0, float(self.active_trade.get(impact_key, 0.0)))
            expected = quantity * impact
            debited = float(self.active_trade.get(cost_key, 0.0))
            remaining = max(0.0, expected - debited)
            if remaining > 1e-9 and impact > 0.0:
                self._debit_active_trade_impact(
                    role=role,
                    quantity=remaining / impact,
                    ts_ns=fallback_ts,
                )

    def on_position_closed(self, event: Any) -> None:
        ts_ns = int(getattr(event, "ts_event", self.clock.timestamp_ns()))
        self._debit_missing_fill_impact_before_close(ts_ns)
        previous_count = len(self.trade_records)
        super().on_position_closed(event)
        if len(self.trade_records) <= previous_count:
            return
        record = self.trade_records[-1]
        entry_cost = float(
            record.get("conservative_entry_impact_cost", 0.0),
        )
        exit_cost = float(
            record.get("conservative_exit_impact_cost", 0.0),
        )
        total = entry_cost + exit_cost
        record["conservative_impact_cost"] = total
        record["impact_adjusted_net_pnl"] = float(record["net_pnl"]) - total
        record["conservative_end_equity"] = (
            self.live_impact_ledger.conservative_equity(
                float(record["end_equity"]),
            )
        )
        conservative_start = float(
            record.get(
                "conservative_start_equity",
                record.get("start_equity", 0.0),
            ),
        )
        record["impact_adjusted_net_return"] = (
            record["conservative_end_equity"] / conservative_start - 1.0
            if conservative_start > 0.0
            else -1.0
        )
        record["impact_ledger_cost_after_close"] = (
            self.live_impact_ledger.cumulative_cost
        )
        self._observe_live_equity()


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size <= 1:
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        return [dict(row) for row in csv.DictReader(stream)]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _impact_event_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trade in trades:
        scenario_id = str(trade.get("scenario_id", ""))
        for role, cost_key, ts_key, fallback_ts_key in (
            (
                "ENTRY",
                "conservative_entry_impact_cost",
                "entry_impact_debit_ts_ns",
                "opened_ts_ns",
            ),
            (
                "EXIT",
                "conservative_exit_impact_cost",
                "exit_impact_debit_ts_ns",
                "closed_ts_ns",
            ),
        ):
            cost = max(0.0, float(trade.get(cost_key, 0.0) or 0.0))
            if cost <= 0.0:
                continue
            ts_ns = int(
                float(
                    trade.get(ts_key, trade.get(fallback_ts_key, 0)) or 0,
                ),
            )
            rows.append(
                {
                    "scenario_id": scenario_id,
                    "role": role,
                    "ts_ns": ts_ns,
                    "cost": cost,
                },
            )
    rows.sort(key=lambda row: (int(row["ts_ns"]), str(row["role"])))
    cumulative = 0.0
    for row in rows:
        cumulative += float(row["cost"])
        row["cumulative_cost"] = cumulative
    return rows


def run_live_cost_backtest(**kwargs: Any) -> dict[str, Any]:
    """Recompute promotion fields from fill-timestamp cost-after evidence."""

    metrics = _BASE_RUN(**kwargs)
    destination = Path(kwargs["output_dir"])
    trades = _read_csv(destination / "trades.csv")
    curve = _read_csv(destination / "equity_curve.csv")
    daily_nav = {
        str(item["date"]): float(item["nav"])
        for item in metrics.get("daily_nav", [])
    }
    adjusted = impact_adjusted_ledger(
        starting_nav=float(metrics["starting_nav"]),
        ending_nav=float(metrics["ending_nav"]),
        daily_nav=daily_nav,
        equity_curve=curve,
        trades=trades,
        tick_max_drawdown=float(metrics["intraday_max_drawdown"]),
    )
    adjusted_curve = adjusted.pop("impact_adjusted_equity_curve")
    _write_csv(
        destination / "impact_adjusted_equity_curve.csv",
        adjusted_curve,
    )
    _write_csv(destination / "impact_cost_events.csv", _impact_event_rows(trades))
    metrics.update(adjusted)

    metrics["engine_wins"] = metrics.get("wins", 0)
    metrics["engine_losses"] = metrics.get("losses", 0)
    metrics["engine_win_rate"] = metrics.get("win_rate", 0.0)
    metrics["engine_profit_concentration_largest_win"] = metrics.get(
        "profit_concentration_largest_win",
        0.0,
    )
    metrics["engine_scenario_net_pnl"] = metrics.get("scenario_net_pnl", {})

    positive: list[float] = []
    negative: list[float] = []
    scenario_pnl: dict[str, float] = defaultdict(float)
    for row in trades:
        pnl = float(row.get("impact_adjusted_net_pnl", 0.0) or 0.0)
        if pnl > 0.0:
            positive.append(pnl)
        elif pnl < 0.0:
            negative.append(pnl)
        scenario_pnl[str(row.get("scenario", "UNKNOWN"))] += pnl
    positive.sort(reverse=True)
    positive_sum = sum(positive)
    metrics["wins"] = len(positive)
    metrics["losses"] = len(negative)
    metrics["win_rate"] = len(positive) / len(trades) if trades else 0.0
    metrics["profit_concentration_largest_win"] = (
        positive[0] / positive_sum
        if positive and positive_sum > 0.0
        else 0.0
    )
    metrics["scenario_net_pnl"] = dict(scenario_pnl)
    metrics["win_loss_basis"] = "IMPACT_ADJUSTED_NET_PNL"

    risk_fraction = float(kwargs.get("risk_fraction", Decimal("0.03")))
    live = live_ledger_diagnostics(
        trades=trades,
        risk_fraction=risk_fraction,
        adjusted_ending_nav=float(metrics["impact_adjusted_ending_nav"]),
    )
    metrics["live_conservative_ledger"] = live
    metrics["candidate_generation"] = (
        "candidate-10-live-fill-time-impact-ledger"
    )
    metrics["cost_model"]["impact"] = (
        "size-dependent expected impact debited from the conservative side "
        "ledger at actual entry and exit fill timestamps"
    )
    metrics["risk"]["sizing_basis"] = live["sizing_basis"]
    metrics["risk"]["modeled_impact_debit_timing"] = live[
        "impact_debit_timing"
    ]
    metrics["target_pass"] = bool(
        metrics["impact_adjusted_geometric_daily_growth"] >= 0.01
        and metrics["closed_trades"] >= 7
        and metrics["wins"] >= 4
        and metrics["profit_concentration_largest_win"] <= 0.50
        and metrics["order_error_count"] == 0
        and metrics["impact_adjusted_intraday_max_drawdown"] < 0.30
        and metrics["causal_gate_pass"]
        and live["risk_budget_violation_count"] == 0
        and live["recorded_vs_reported_ending_nav_match"]
    )
    _research.write_json_atomic(destination / "metrics.json", metrics)
    run_path = destination / "run.json"
    if run_path.exists():
        run_manifest = json.loads(run_path.read_text(encoding="utf-8"))
        run_manifest["live_cost_ledger"] = True
        run_manifest["risk_sizing_basis"] = live["sizing_basis"]
        run_manifest["impact_debit_timing"] = live["impact_debit_timing"]
        _research.write_json_atomic(run_path, run_manifest)
    return metrics


def install_live_cost_ledger() -> None:
    """Install after the desired target/state strategy has been selected."""

    if getattr(_research, "_C10_LIVE_COST_LEDGER_INSTALLED", False):
        return
    _research.LiquidationCandidate10Strategy = LiveCostLiquidationStrategy
    _research.run_liquidation_backtest = run_live_cost_backtest
    _research._C10_LIVE_COST_LEDGER_INSTALLED = True


__all__ = [
    "LiveCostLiquidationStrategy",
    "install_live_cost_ledger",
    "run_live_cost_backtest",
]
