"""Position entry, protection, accounting, and full-exit lifecycle."""
from __future__ import annotations

import math
from typing import Mapping

from domain_v3 import Side, TradePlan, size_for_fixed_risk
from simulator_v3_types import Position, TradeRecord


class PositionEngineMixin:
    def _enter(self, plan: TradePlan, ts_open_ns: int, ts_close_ns: int, path_after_entry: list[float]) -> None:
        spec = self.specs[plan.symbol]
        quantity, _, planned = size_for_fixed_risk(
            nav=self._marked_equity({plan.symbol: plan.entry}),
            risk_fraction=self.risk_fraction,
            plan=plan,
            costs=self.costs,
            size_increment=spec.size_increment,
        )
        if quantity < spec.min_quantity or quantity * plan.entry < spec.min_notional:
            self.diagnostics["minimum_size_rejected"] += 1
            return
        notional = quantity * plan.entry
        entry_fee = notional * self.costs.entry_fee_bps / 10_000.0
        entry_slippage = notional * self.costs.entry_slippage_bps / 10_000.0
        nav_before = self.nav
        self.nav -= entry_fee + entry_slippage
        self.position = Position(
            plan=plan,
            quantity=quantity,
            entry_time_ns=ts_open_ns,
            nav_before=nav_before,
            entry_fee_and_slippage=entry_fee + entry_slippage,
            planned_account_loss=planned,
            entry_notional=notional,
        )
        self.position_funding = 0.0
        self.diagnostics["entries"] += 1
        self.diagnostics[f"entries_{plan.family}"] += 1
        self.diagnostics["max_entry_notional_to_nav"] = max(
            float(self.diagnostics.get("max_entry_notional_to_nav", 0.0)),
            notional / nav_before if nav_before > 0.0 else math.inf,
        )
        self._process_position(path_after_entry, ts_close_ns)

    def _process_position(self, path: list[float], ts_close_ns: int) -> None:
        position = self.position
        if position is None or not path:
            return
        plan = position.plan
        open_price = path[0]
        if plan.side is Side.LONG and open_price <= plan.stop:
            self._exit("STOP", ts_close_ns, gap_price=open_price)
            return
        if plan.side is Side.SHORT and open_price >= plan.stop:
            self._exit("STOP", ts_close_ns, gap_price=open_price)
            return
        for idx in range(len(path) - 1):
            a, b = path[idx], path[idx + 1]
            events = self._distance_order(a, b, {"STOP": plan.stop, "TARGET": plan.target})
            if events:
                self._exit(events[0][0], ts_close_ns)
                return

    def _apply_funding(self, symbol: str, ts_close_ns: int, close: float, funding_rate: float | None) -> None:
        position = self.position
        if position is None or position.plan.symbol != symbol:
            return
        seconds = ts_close_ns // 1_000_000_000
        minute = (seconds // 60) % (24 * 60)
        if minute not in {0, 8 * 60, 16 * 60}:
            return
        rate = self.default_funding_rate if funding_rate is None else float(funding_rate)
        charge = position.quantity * close * abs(rate)
        self.nav -= charge
        self.position_funding += charge
        self.diagnostics["funding_settlements"] += 1

    def _exit(self, outcome: str, ts_exit_ns: int, *, gap_price: float | None = None) -> None:
        position = self.position
        if position is None:
            return
        plan = position.plan
        qty = position.quantity
        if outcome == "STOP":
            adverse = self.costs.stop_slippage_bps / 10_000.0
            cost_free_exit = plan.stop if gap_price is None else gap_price
            exit_price = cost_free_exit * (1.0 - adverse if plan.side is Side.LONG else 1.0 + adverse)
            exit_fee = qty * exit_price * self.costs.stop_fee_bps / 10_000.0
            exit_slippage = qty * abs(exit_price - cost_free_exit)
        else:
            adverse = self.costs.target_slippage_bps / 10_000.0
            cost_free_exit = plan.target
            exit_price = cost_free_exit * (1.0 - adverse if plan.side is Side.LONG else 1.0 + adverse)
            exit_fee = qty * exit_price * self.costs.target_fee_bps / 10_000.0
            exit_slippage = qty * abs(exit_price - cost_free_exit)

        gross = qty * (cost_free_exit - plan.entry) * int(plan.side)
        realized_price_pnl = qty * (exit_price - plan.entry) * int(plan.side)
        entry_fee_and_slip = position.entry_fee_and_slippage
        fees = (position.entry_notional * self.costs.entry_fee_bps / 10_000.0) + exit_fee
        entry_slippage = position.entry_notional * self.costs.entry_slippage_bps / 10_000.0
        total_slippage = entry_slippage + exit_slippage
        nav_before = position.nav_before
        self.nav += realized_price_pnl - exit_fee
        net = self.nav - nav_before
        risk_basis = position.planned_account_loss if position.planned_account_loss > 0.0 else math.nan
        gross_r = gross / risk_basis
        net_r = net / risk_basis
        cost_r = gross_r - net_r
        hold_minutes = max(0, int((ts_exit_ns - position.entry_time_ns) / 60_000_000_000))
        self.trades.append(
            TradeRecord(
                plan_id=plan.plan_id,
                causal_event_id=plan.causal_event_id,
                symbol=plan.symbol,
                family=plan.family,
                target_mode=plan.target_mode,
                side=int(plan.side),
                signal_time_ns=plan.observed_time_ns,
                entry_time_ns=position.entry_time_ns,
                exit_time_ns=ts_exit_ns,
                entry=plan.entry,
                exit=exit_price,
                stop=plan.stop,
                target=plan.target,
                gross_rr=plan.gross_rr,
                quantity=qty,
                outcome=outcome,
                gross_pnl=gross,
                fees=fees,
                slippage=total_slippage,
                funding=self.position_funding,
                net_pnl=net,
                gross_r=gross_r,
                cost_r=cost_r,
                net_r=net_r,
                nav_before=nav_before,
                nav_after=self.nav,
                planned_account_loss=position.planned_account_loss,
                entry_notional=position.entry_notional,
                entry_notional_to_nav=(position.entry_notional / nav_before if nav_before > 0.0 else math.inf),
                context_bias=plan.context_bias,
                source_timeframe_minutes=plan.source_timeframe_minutes,
                body_ratio=plan.body_ratio,
                previous_body=plan.previous_body,
                current_body=plan.current_body,
                hold_minutes=hold_minutes,
            ),
        )
        self.position = None
        self.position_funding = 0.0
        self.diagnostics[f"exits_{outcome.lower()}"] += 1

    def _marked_equity(self, closes: Mapping[str, float]) -> float:
        if self.position is None:
            return self.nav
        close = closes.get(self.position.plan.symbol)
        if close is None:
            return self.nav
        unrealized = self.position.quantity * (float(close) - self.position.plan.entry) * int(self.position.plan.side)
        return self.nav + unrealized
