"""Controlled size-dependent market-impact overlay for candidate 10 v20.1.

The v20 signal state machine, pools, OI ablation, entry trigger, stop, target,
fees, seed and 3% current-NAV loss budget are unchanged. This module changes
only execution-cost estimation, risk quantity and conservative NAV reporting.
"""
from __future__ import annotations

import csv
from dataclasses import replace
from decimal import Decimal, ROUND_FLOOR
import json
from pathlib import Path
from statistics import median
from typing import Any

from nautilus_trader.model.enums import OrderSide

import c10_liquidation_state as _state
import c10_liquidation_strategy as _strategy
import c10_liquidation_research as _research
from c10_liquidation_state import FiveMinuteAuctionBar, LiquidationPlan
from v20_impact_math import impact_adjusted_ledger, solve_risk_quantity


class ImpactAwareLiquidationAuctionStateMachine(
    _state.LiquidationAuctionStateMachine,
):
    """Attach a causal market-depth scale without changing detector decisions."""

    def _feature_snapshot(self) -> dict[str, float] | None:
        features = super()._feature_snapshot()
        if features is None:
            return None
        result = dict(features)
        result["volume_median"] = max(1.0, float(median(self.volume_history)))
        return result

    def _build_plan(
        self,
        *,
        bar: FiveMinuteAuctionBar,
        probe: Any,
        features: dict[str, float],
        direction: int,
    ) -> LiquidationPlan | None:
        plan = super()._build_plan(
            bar=bar,
            probe=probe,
            features=features,
            direction=direction,
        )
        if plan is None:
            return None
        details = dict(plan.details)
        details.update(
            {
                "causal_liquidity_notional": features["volume_median"],
                "base_impact_per_side": max(
                    plan.expected_entry_impact,
                    plan.expected_stop_impact,
                ),
            },
        )
        return replace(plan, details=details)


class ImpactControlledLiquidationStrategy(
    _strategy.LiquidationCandidate10Strategy,
):
    """Keep v20 signals but solve quantity and impact as one fixed point."""

    def _observe_equity(self, ts_ns: int, *, append_curve: bool) -> None:
        equity = self._equity()
        self.last_equity = equity
        self.max_equity = max(self.max_equity, equity)
        if self.max_equity > 0.0:
            self.max_drawdown = max(
                self.max_drawdown,
                1.0 - equity / self.max_equity,
            )
        if append_curve:
            self.equity_curve.append({"ts_ns": ts_ns, "equity": equity})

    def _record_equity(self, ts_ns: int) -> None:
        self._observe_equity(ts_ns, append_curve=True)

    def _quantity_for_plan(
        self,
        plan: LiquidationPlan,
        entry: float,
    ) -> tuple[Any, dict[str, float]] | None:
        assert self.instrument is not None
        stop = float(plan.stop_price)
        target = float(plan.target_price)
        valid = stop < entry < target if plan.direction > 0 else target < entry < stop
        if not valid:
            return None

        risk_budget = self._equity() * float(self.config.risk_fraction)
        atr = float(plan.details.get("atr", 0.0))
        liquidity_notional = float(
            plan.details.get("causal_liquidity_notional", 0.0),
        )
        base_impact = max(
            float(plan.expected_entry_impact),
            float(plan.expected_stop_impact),
        )
        solution = solve_risk_quantity(
            risk_budget=risk_budget,
            entry=entry,
            stop=stop,
            taker_fee=float(self.instrument.taker_fee),
            base_impact=base_impact,
            atr=atr,
            liquidity_notional=liquidity_notional,
        )
        if solution is None:
            return None

        increment = Decimal(str(self.instrument.size_increment))
        raw_qty = Decimal(str(solution.quantity))
        units = (raw_qty / increment).to_integral_value(rounding=ROUND_FLOOR)
        value = units * increment
        if value < Decimal(str(self.instrument.min_quantity)):
            return None
        quantity = self.instrument.make_qty(value)

        rounded_notional = quantity.as_double() * entry
        participation = (
            rounded_notional / liquidity_notional
            if liquidity_notional > 0.0
            else float("inf")
        )
        size_impact = (
            atr * participation**0.5
            if participation >= 0.0
            else float("inf")
        )
        impact = max(base_impact, size_impact)
        entry_d = Decimal(str(entry))
        stop_d = Decimal(str(stop))
        target_d = Decimal(str(target))
        taker = Decimal(str(self.instrument.taker_fee))
        impact_d = Decimal(str(impact))
        per_unit_loss = (
            abs(entry_d - stop_d)
            + entry_d * taker
            + stop_d * taker
            + Decimal("2") * impact_d
        )
        gross_reward = (
            target_d - entry_d
            if plan.direction > 0
            else entry_d - target_d
        )
        net_reward = (
            gross_reward
            - entry_d * taker
            - target_d * taker
            - Decimal("2") * impact_d
        )
        executable_rr = (
            float(net_reward / per_unit_loss)
            if per_unit_loss > 0
            else float("-inf")
        )
        minimum_rr = float(self.config.params.get("min_net_rr", 1.35))
        if executable_rr < minimum_rr:
            return None

        planned_loss = quantity.as_double() * float(per_unit_loss)
        tolerance = max(0.01, risk_budget * 1e-9)
        if planned_loss > risk_budget + tolerance:
            raise RuntimeError(
                "rounded quantity exceeds planned loss budget: "
                f"{planned_loss} > {risk_budget}",
            )
        return quantity, {
            "impact_per_side": impact,
            "base_impact_per_side": base_impact,
            "participation": participation,
            "per_unit_loss": float(per_unit_loss),
            "planned_loss": planned_loss,
            "risk_budget": risk_budget,
            "executable_net_rr": executable_rr,
            "causal_liquidity_notional": liquidity_notional,
        }

    def _submit_entry(self, plan: LiquidationPlan, tick: Any) -> bool:
        assert self.instrument is not None
        entry = tick.price.as_double()
        sizing = self._quantity_for_plan(plan, entry)
        if sizing is None:
            self.plan_gap_rejections += 1
            return False
        quantity, execution_plan = sizing
        side = OrderSide.BUY if plan.direction > 0 else OrderSide.SELL
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            reduce_only=False,
            tags=["LIQUIDATION_AUCTION_ENTRY"],
        )
        start_equity = self._equity()
        self.entry_order_client_id = order.client_order_id
        self.active_trade = {
            "scenario_id": plan.scenario_id,
            "scenario": plan.scenario,
            "direction": plan.direction,
            "signal_ts_ns": plan.observed_ns,
            "entry_submit_ts_ns": int(tick.ts_event),
            "entry_estimate": entry,
            "stop": plan.stop_price,
            "target": plan.target_price,
            "quantity": quantity.as_double(),
            "start_equity": start_equity,
            "source_pool_id": plan.source_pool_id,
            "target_pool_id": plan.target_pool_id,
            "expected_entry_impact": execution_plan["impact_per_side"],
            "expected_exit_impact": execution_plan["impact_per_side"],
            "base_entry_impact": plan.expected_entry_impact,
            "base_exit_impact": plan.expected_stop_impact,
            "expected_participation": execution_plan["participation"],
            "causal_liquidity_notional": execution_plan[
                "causal_liquidity_notional"
            ],
            "planned_loss": execution_plan["planned_loss"],
            "planned_loss_budget": execution_plan["risk_budget"],
            "planned_per_unit_loss": execution_plan["per_unit_loss"],
            "cost_adjusted_net_rr": execution_plan["executable_net_rr"],
            "logic_details": dict(plan.details),
            "entry_order_type": "MARKET_ON_FIRST_POST_CONFIRMATION_TRADE",
            "event_state": "ORDER_PENDING",
        }
        self.orders_submitted += 1
        self._append_execution_event(
            event_type="ORDER_SUBMITTED",
            reason_code="NAUTILUS_NEXT_TRADE_MARKET_ENTRY",
            ts_ns=int(tick.ts_event),
            previous_state="ENTRY_READY",
            next_state="ORDER_PENDING",
            reference_price=entry,
            details={
                "quantity": quantity.as_double(),
                "stop": plan.stop_price,
                "target": plan.target_price,
                "risk_fraction": str(self.config.risk_fraction),
                "expected_entry_impact": execution_plan["impact_per_side"],
                "expected_exit_impact": execution_plan["impact_per_side"],
                "expected_participation": execution_plan["participation"],
                "planned_loss": execution_plan["planned_loss"],
                "planned_loss_budget": execution_plan["risk_budget"],
            },
        )
        self.submit_order(order)
        return True

    def on_trade_tick(self, tick: Any) -> None:
        if self.active_trade is not None and not self.portfolio.is_flat(
            self.config.instrument_id,
        ):
            self._observe_equity(int(tick.ts_event), append_curve=False)
        super().on_trade_tick(tick)

    def on_order_filled(self, event: Any) -> None:
        if self.active_trade is None:
            return
        if getattr(event, "client_order_id", None) != self.entry_order_client_id:
            return
        fill_qty = event.last_qty.as_double()
        fill_px = event.last_px.as_double()
        previous_qty = float(self.active_trade.get("actual_entry_qty", 0.0))
        previous_px = float(
            self.active_trade.get("actual_entry_price", fill_px),
        )
        total_qty = previous_qty + fill_qty
        weighted_px = (
            (previous_px * previous_qty + fill_px * fill_qty) / total_qty
            if total_qty > 0.0
            else fill_px
        )
        self.active_trade["actual_entry_price"] = weighted_px
        self.active_trade["actual_entry_qty"] = total_qty

    def on_position_opened(self, event: Any) -> None:
        super().on_position_opened(event)
        self._observe_equity(
            int(getattr(event, "ts_event", self.clock.timestamp_ns())),
            append_curve=False,
        )

    def on_position_closed(self, event: Any) -> None:
        previous_count = len(self.trade_records)
        super().on_position_closed(event)
        if len(self.trade_records) <= previous_count:
            return
        record = self.trade_records[-1]
        quantity = float(record.get("actual_entry_qty", record.get("quantity", 0.0)))
        entry_impact = float(record.get("expected_entry_impact", 0.0))
        exit_impact = float(record.get("expected_exit_impact", entry_impact))
        impact_cost = quantity * (entry_impact + exit_impact)
        record["conservative_impact_cost"] = impact_cost
        record["impact_adjusted_net_pnl"] = float(record["net_pnl"]) - impact_cost
        self._observe_equity(
            int(getattr(event, "ts_event", self.clock.timestamp_ns())),
            append_curve=False,
        )


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


_ORIGINAL_RUN = _research.run_liquidation_backtest


def run_impact_controlled_backtest(**kwargs: Any) -> dict[str, Any]:
    metrics = _ORIGINAL_RUN(**kwargs)
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
    _write_csv(destination / "impact_adjusted_equity_curve.csv", adjusted_curve)
    metrics.update(adjusted)
    metrics["signal_generation"] = "v20-liquidation-auction-rejection-acceptance"
    metrics["candidate_generation"] = (
        "v20.1-liquidation-auction-size-dependent-impact-control"
    )
    metrics["execution_cost_control"] = (
        "ATR_SQRT_ORDER_NOTIONAL_OVER_CAUSAL_MEDIAN_5M_QUOTE_VOLUME"
    )
    metrics["cost_model"]["impact"] = (
        "larger of the causal stress floor and ATR times square root of "
        "order notional divided by causal median 5m quote volume; the "
        "estimate is debited from conservative impact-adjusted NAV"
    )
    metrics["risk"]["planned_loss_components"] = (
        "entry-to-stop + entry/stop taker fees + fixed-point size-dependent "
        "expected entry/exit impact"
    )
    metrics["target_pass"] = bool(
        metrics["impact_adjusted_geometric_daily_growth"] >= 0.01
        and metrics["closed_trades"] >= 7
        and metrics["wins"] >= 4
        and metrics["profit_concentration_largest_win"] <= 0.50
        and metrics["order_error_count"] == 0
        and metrics["impact_adjusted_intraday_max_drawdown"] < 0.30
        and metrics["causal_gate_pass"]
    )
    _research.write_json_atomic(destination / "metrics.json", metrics)
    run_path = destination / "run.json"
    if run_path.exists():
        run_manifest = json.loads(run_path.read_text(encoding="utf-8"))
        run_manifest["candidate_generation"] = metrics["candidate_generation"]
        run_manifest["signal_generation"] = metrics["signal_generation"]
        run_manifest["execution_cost_control"] = metrics[
            "execution_cost_control"
        ]
        _research.write_json_atomic(run_path, run_manifest)
    return metrics


def install() -> None:
    """Install the execution-cost control idempotently."""
    if getattr(_research, "_V20_IMPACT_CONTROL_INSTALLED", False):
        return
    _strategy.LiquidationAuctionStateMachine = (
        ImpactAwareLiquidationAuctionStateMachine
    )
    _research.LiquidationCandidate10Strategy = (
        ImpactControlledLiquidationStrategy
    )
    _research.run_liquidation_backtest = run_impact_controlled_backtest
    _research._V20_IMPACT_CONTROL_INSTALLED = True


__all__ = [
    "ImpactAwareLiquidationAuctionStateMachine",
    "ImpactControlledLiquidationStrategy",
    "install",
    "run_impact_controlled_backtest",
]
