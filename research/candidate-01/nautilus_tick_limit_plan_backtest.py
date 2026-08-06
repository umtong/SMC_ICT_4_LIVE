"""Authoritative NautilusTrader TradeTick execution for causal resting entries.

Candidate logic supplies completed ScenarioPlan objects plus a causal resting
entry instruction. Official Binance Vision USD-M aggregate trades are converted
one-for-one into NautilusTrader TradeTick objects. NautilusTrader exclusively
owns limit-entry matching, contingent orders, commissions, margin, positions,
account equity and reports. No fill, stop/target, PnL or NAV simulator exists.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from aggtrade_data import AggTrade
from core import Side
from impact_regime_probe import ScenarioPlan
from nautilus_plan_backtest import (
    GlobalEntryGate,
    NautilusExecutionConfig,
    NautilusRunEvidence,
    _as_float,
    _atomic_json,
    _build_metrics,
    _json_safe,
    _money_from_equity_map,
    _utc_date,
    _write_jsonl,
)


@dataclass(frozen=True, slots=True)
class RestingEntryInstruction:
    """Immutable plan plus its causal resting-entry contract."""

    plan: ScenarioPlan
    entry_price: float
    expiry_time_ns: int
    entry_reason: str

    def __post_init__(self) -> None:
        if self.entry_price <= 0.0:
            raise ValueError("entry_price must be positive")
        if self.expiry_time_ns <= int(self.plan.signal_time_ns):
            raise ValueError("expiry_time_ns must be after signal_time_ns")


def run_nautilus_tick_limit_plan_backtest(
    *,
    label: str,
    trades: Sequence[AggTrade],
    instructions: Sequence[RestingEntryInstruction],
    evaluation_start: datetime,
    evaluation_end: datetime,
    execution: NautilusExecutionConfig,
    maximum_hold_ns: int,
    output_dir: Path,
) -> NautilusRunEvidence:
    """Execute resting-entry brackets through NautilusTrader only."""

    if evaluation_end <= evaluation_start:
        raise ValueError("evaluation_end must be after evaluation_start")
    if maximum_hold_ns <= 0:
        raise ValueError("maximum_hold_ns must be positive")
    if not trades:
        raise ValueError("trades cannot be empty")

    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import (
        BacktestEngineConfig,
        LoggingConfig,
        StrategyConfig,
    )
    from nautilus_trader.model.data import TradeTick
    from nautilus_trader.model.enums import (
        AccountType,
        AggressorSide,
        OmsType,
        OrderSide,
        OrderType,
        TimeInForce,
    )
    from nautilus_trader.model.events import PositionClosed, PositionOpened
    from nautilus_trader.model.identifiers import (
        InstrumentId,
        Symbol,
        TradeId,
        Venue,
    )
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity
    from nautilus_trader.trading.strategy import Strategy

    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)
    ordered_trades = sorted(
        trades,
        key=lambda item: (item.ts_event_ns, item.agg_trade_id),
    )
    prior_time = -1
    prior_id = -1
    evaluation_count = 0
    post_end_count = 0
    for trade in ordered_trades:
        if trade.ts_event_ns < prior_time:
            raise ValueError("execution trade timestamp regression")
        if trade.agg_trade_id <= prior_id:
            raise ValueError("execution aggregate-trade ID regression")
        prior_time = trade.ts_event_ns
        prior_id = trade.agg_trade_id
        if start_ns <= trade.ts_event_ns < end_ns:
            evaluation_count += 1
        elif trade.ts_event_ns >= end_ns:
            post_end_count += 1
    if evaluation_count <= 0:
        raise ValueError("no official trade ticks in evaluation interval")
    if post_end_count < 3:
        raise ValueError("at least three post-evaluation flush ticks are required")

    eligible = [
        item
        for item in sorted(
            instructions,
            key=lambda row: (row.plan.signal_time_ns, row.plan.scenario_id),
        )
        if start_ns <= int(item.plan.signal_time_ns) < end_ns
    ]

    class RestingPlanStrategyConfig(StrategyConfig, frozen=True):
        instrument_id: InstrumentId
        risk_fraction: Decimal
        cost_fraction_per_side: Decimal
        minimum_net_reward_risk: Decimal
        minimum_price_risk_fraction: Decimal
        evaluation_start_ns: int
        evaluation_end_ns: int
        maximum_hold_ns: int

    class RestingPlanStrategy(Strategy):
        def __init__(
            self,
            config: RestingPlanStrategyConfig,
            *,
            instruction_sequence: Sequence[RestingEntryInstruction],
            gate: GlobalEntryGate,
            instrument: CryptoPerpetual,
        ) -> None:
            super().__init__(config)
            self.instructions = instruction_sequence
            self.plan_cursor = 0
            self.gate = gate
            self.instrument = instrument
            self.active_owner: str | None = None
            self.pending_instruction: RestingEntryInstruction | None = None
            self.pending_submission_ns: int | None = None
            self.pending_entry_price: float | None = None
            self.pending_stop_price: float | None = None
            self.pending_target_price: float | None = None
            self.position_opened_ns: int | None = None
            self.end_exit_submitted = False
            self.submissions: list[dict[str, Any]] = []
            self.rejections: list[dict[str, Any]] = []
            self.execution_events: list[dict[str, Any]] = []
            self.daily_nav: list[dict[str, Any]] = []
            self.start_nav: float | None = None
            self.final_nav: float | None = None
            self._current_day: str | None = None
            self._last_day_equity: float | None = None
            self._high_water: float | None = None
            self._last_ts_ns = 0
            self.max_drawdown = 0.0
            self.gate_violations = 0
            self.protective_order_failures = 0
            self.maximum_hold_exits = 0
            self.minimum_equity_to_maintenance_margin: float | None = None
            self.ended_flat = True
            self.limit_entries_expired = 0
            self.targets_consumed_before_entry = 0
            self.pending_entries_canceled_at_end = 0

        def on_start(self) -> None:
            self.subscribe_trade_ticks(self.config.instrument_id)

        def _record(self, event_type: str, ts_ns: int, **details: Any) -> None:
            self.execution_events.append(
                {
                    "event_type": event_type,
                    "observed_time_ns": ts_ns,
                    "details": _json_safe(details),
                },
            )

        def _reject(
            self,
            instruction: RestingEntryInstruction,
            *,
            ts_ns: int,
            reason: str,
            **details: Any,
        ) -> None:
            plan = instruction.plan
            row = {
                "scenario_id": plan.scenario_id,
                "signal_time_ns": plan.signal_time_ns,
                "entry_evaluation_time_ns": ts_ns,
                "side": plan.side.value,
                "response": plan.response,
                "planned_entry_price": instruction.entry_price,
                "entry_expiry_time_ns": instruction.expiry_time_ns,
                "entry_reason": instruction.entry_reason,
                "reason": reason,
                **details,
            }
            self.rejections.append(row)
            self._record("PLAN_REJECTED", ts_ns, **row)

        def _equity(self) -> float:
            equity = self.portfolio.equity(self.config.instrument_id.venue)
            return _money_from_equity_map(equity, Currency.from_str("USDT"))

        def _mark_nav(self, ts_ns: int) -> None:
            if ts_ns < self.config.evaluation_start_ns:
                return
            equity = self._equity()
            if self.start_nav is None:
                self.start_nav = equity
                self._high_water = equity
            effective_ts = min(ts_ns, self.config.evaluation_end_ns - 1)
            day = _utc_date(effective_ts)
            if self._current_day is None:
                self._current_day = day
            elif day != self._current_day:
                assert self._last_day_equity is not None
                self.daily_nav.append(
                    {"date": self._current_day, "nav": self._last_day_equity},
                )
                self._current_day = day
            self._last_day_equity = equity
            self.final_nav = equity
            if self._high_water is None or equity > self._high_water:
                self._high_water = equity
            if self._high_water > 0.0:
                self.max_drawdown = min(
                    self.max_drawdown,
                    equity / self._high_water - 1.0,
                )
            margins = self.portfolio.margins_maint(
                self.config.instrument_id.venue,
            ) or {}
            try:
                maintenance = _money_from_equity_map(
                    margins,
                    Currency.from_str("USDT"),
                )
            except RuntimeError:
                maintenance = 0.0
            if maintenance > 0.0:
                ratio = equity / maintenance
                if self.minimum_equity_to_maintenance_margin is None:
                    self.minimum_equity_to_maintenance_margin = ratio
                else:
                    self.minimum_equity_to_maintenance_margin = min(
                        self.minimum_equity_to_maintenance_margin,
                        ratio,
                    )

        def _mark_nav_if_needed(self, ts_ns: int) -> None:
            effective_ts = min(ts_ns, self.config.evaluation_end_ns - 1)
            day = _utc_date(effective_ts)
            if (
                self.start_nav is None
                or day != self._current_day
                or not self.portfolio.is_flat(self.config.instrument_id)
                or ts_ns >= self.config.evaluation_end_ns
            ):
                self._mark_nav(ts_ns)

        def _release_gate(self) -> None:
            if self.active_owner is not None:
                self.gate.release(self.active_owner)
                self.active_owner = None

        def _clear_pending(self) -> None:
            self.pending_instruction = None
            self.pending_submission_ns = None
            self.pending_entry_price = None
            self.pending_stop_price = None
            self.pending_target_price = None

        def _cancel_pending(
            self,
            *,
            ts_ns: int,
            reason: str,
            observed_price: float,
        ) -> None:
            instruction = self.pending_instruction
            if instruction is None:
                return
            self.cancel_all_orders(self.config.instrument_id)
            self._reject(
                instruction,
                ts_ns=ts_ns,
                reason=reason,
                observed_price=observed_price,
                pending_submission_ns=self.pending_submission_ns,
            )
            self._record(
                "PENDING_LIMIT_CANCELED",
                ts_ns,
                scenario_id=instruction.plan.scenario_id,
                reason=reason,
                observed_price=observed_price,
            )
            self._clear_pending()
            self._release_gate()

        def _flatten(self, *, ts_ns: int, reason: str) -> None:
            if self.pending_instruction is not None:
                self.cancel_all_orders(self.config.instrument_id)
                self._clear_pending()
            if self.portfolio.is_flat(self.config.instrument_id):
                self._release_gate()
                return
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(
                self.config.instrument_id,
                reduce_only=True,
            )
            self._record(reason, ts_ns)

        def _time_exit_if_needed(self, ts_ns: int) -> None:
            if self.position_opened_ns is None:
                return
            if ts_ns - self.position_opened_ns < self.config.maximum_hold_ns:
                return
            self.maximum_hold_exits += 1
            self._flatten(ts_ns=ts_ns, reason="MAXIMUM_HOLD_EXIT_SUBMITTED")
            self.position_opened_ns = None

        def _geometry(
            self,
            instruction: RestingEntryInstruction,
            *,
            ts_ns: int,
        ) -> tuple[float, float, float, float, float, float] | None:
            plan = instruction.plan
            entry = _as_float(self.instrument.make_price(instruction.entry_price))
            stop = _as_float(self.instrument.make_price(plan.stop_price))
            target = _as_float(self.instrument.make_price(plan.target_price))
            rounded_hold = _as_float(
                self.instrument.make_price(plan.confirmation_hold_price),
            )
            hold_ok = (
                entry >= rounded_hold if plan.side is Side.LONG else entry <= rounded_hold
            )
            if not hold_ok:
                self._reject(
                    instruction,
                    ts_ns=ts_ns,
                    reason="ENTRY_LIMIT_OUTSIDE_CONFIRMATION_CONTRACT",
                    entry=entry,
                    rounded_hold=rounded_hold,
                    stop=stop,
                    target=target,
                )
                return None
            geometry_ok = (
                stop < entry < target
                if plan.side is Side.LONG
                else target < entry < stop
            )
            if not geometry_ok:
                self._reject(
                    instruction,
                    ts_ns=ts_ns,
                    reason="INVALID_LIMIT_ENTRY_GEOMETRY",
                    entry=entry,
                    stop=stop,
                    target=target,
                )
                return None

            cost = float(self.config.cost_fraction_per_side)
            price_risk = abs(entry - stop)
            planned_loss = price_risk + entry * cost + stop * cost
            planned_gain = abs(target - entry) - entry * cost - target * cost
            price_fraction = (
                price_risk / planned_loss if planned_loss > 0.0 else 0.0
            )
            net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
            if price_fraction < float(self.config.minimum_price_risk_fraction):
                self._reject(
                    instruction,
                    ts_ns=ts_ns,
                    reason="COST_DOMINATED_AT_LIMIT_ENTRY",
                    entry=entry,
                    stop=stop,
                    target=target,
                    price_risk_fraction=price_fraction,
                    net_reward_risk=net_rr,
                )
                return None
            if planned_gain <= 0.0 or net_rr < float(
                self.config.minimum_net_reward_risk,
            ):
                self._reject(
                    instruction,
                    ts_ns=ts_ns,
                    reason="INSUFFICIENT_NET_REWARD_RISK_AT_LIMIT_ENTRY",
                    entry=entry,
                    stop=stop,
                    target=target,
                    price_risk_fraction=price_fraction,
                    net_reward_risk=net_rr,
                )
                return None
            return entry, stop, target, planned_loss, price_fraction, net_rr

        def _manage_pending(self, tick: TradeTick) -> bool:
            instruction = self.pending_instruction
            if instruction is None:
                return False
            if not self.portfolio.is_flat(self.config.instrument_id):
                self._clear_pending()
                return False
            ts_ns = int(tick.ts_event)
            price = _as_float(tick.price)
            plan = instruction.plan
            target = float(self.pending_target_price or plan.target_price)
            target_consumed = (
                price >= target if plan.side is Side.LONG else price <= target
            )
            if target_consumed:
                self.targets_consumed_before_entry += 1
                self._cancel_pending(
                    ts_ns=ts_ns,
                    reason="TARGET_CONSUMED_BEFORE_LIMIT_ENTRY",
                    observed_price=price,
                )
                return True
            if ts_ns >= int(instruction.expiry_time_ns):
                self.limit_entries_expired += 1
                self._cancel_pending(
                    ts_ns=ts_ns,
                    reason="LIMIT_ENTRY_RESPONSE_WINDOW_EXPIRED",
                    observed_price=price,
                )
                return True
            return False

        def _submit_due(
            self,
            tick: TradeTick,
            due: Sequence[RestingEntryInstruction],
        ) -> None:
            if not due:
                return
            ts_ns = int(tick.ts_event)
            current = _as_float(tick.price)
            if (
                not self.portfolio.is_flat(self.config.instrument_id)
                or self.pending_instruction is not None
            ):
                for instruction in due:
                    self._reject(
                        instruction,
                        ts_ns=ts_ns,
                        reason="GLOBAL_POSITION_OR_PENDING_ENTRY_OCCUPIED",
                        observed_price=current,
                    )
                return
            if self.gate.owner is not None:
                self.gate_violations += 1
                for instruction in due:
                    self._reject(
                        instruction,
                        ts_ns=ts_ns,
                        reason="GLOBAL_ENTRY_GATE_OCCUPIED",
                        observed_price=current,
                    )
                return

            viable: list[
                tuple[
                    float,
                    RestingEntryInstruction,
                    float,
                    float,
                    float,
                    float,
                    float,
                ]
            ] = []
            for instruction in due:
                plan = instruction.plan
                if ts_ns >= instruction.expiry_time_ns:
                    self._reject(
                        instruction,
                        ts_ns=ts_ns,
                        reason="LIMIT_ENTRY_ALREADY_EXPIRED_AT_ARMING",
                        observed_price=current,
                    )
                    continue
                rounded_hold = _as_float(
                    self.instrument.make_price(plan.confirmation_hold_price),
                )
                current_hold = (
                    current >= rounded_hold
                    if plan.side is Side.LONG
                    else current <= rounded_hold
                )
                if not current_hold:
                    self._reject(
                        instruction,
                        ts_ns=ts_ns,
                        reason="FAILED_CONFIRMATION_HOLD_BEFORE_LIMIT_ARMING",
                        observed_price=current,
                        rounded_hold=rounded_hold,
                    )
                    continue
                geometry = self._geometry(instruction, ts_ns=ts_ns)
                if geometry is None:
                    continue
                entry, stop, target, planned_loss, price_fraction, net_rr = geometry
                passive_or_touch = (
                    entry <= current if plan.side is Side.LONG else entry >= current
                )
                if not passive_or_touch:
                    self._reject(
                        instruction,
                        ts_ns=ts_ns,
                        reason="LIMIT_ENTRY_WOULD_CHASE_AFTER_HOLD_FAILURE",
                        observed_price=current,
                        entry=entry,
                    )
                    continue
                viable.append(
                    (
                        net_rr,
                        instruction,
                        entry,
                        stop,
                        target,
                        planned_loss,
                        price_fraction,
                    ),
                )
            if not viable:
                return
            ordered = sorted(
                viable,
                key=lambda row: (-row[0], row[1].plan.scenario_id),
            )
            (
                net_rr,
                instruction,
                entry,
                stop,
                target,
                planned_loss,
                price_fraction,
            ) = ordered[0]
            for _, competing, *_ in ordered[1:]:
                self._reject(
                    competing,
                    ts_ns=ts_ns,
                    reason="LOWER_NET_RR_COMPETING_LIMIT_PLAN",
                    observed_price=current,
                )

            plan = instruction.plan
            if not self.gate.acquire(plan.scenario_id):
                self.gate_violations += 1
                self._reject(
                    instruction,
                    ts_ns=ts_ns,
                    reason="GLOBAL_ENTRY_GATE_ACQUIRE_FAILED",
                )
                return

            equity = self._equity()
            risk_budget = equity * float(self.config.risk_fraction)
            raw_quantity = risk_budget / planned_loss
            quantity = self.instrument.make_qty(raw_quantity, round_down=True)
            quantity_value = _as_float(quantity)
            if quantity_value <= 0.0:
                self.gate.release(plan.scenario_id)
                self._reject(
                    instruction,
                    ts_ns=ts_ns,
                    reason="ZERO_QUANTITY_AFTER_PRECISION",
                )
                return

            side = OrderSide.BUY if plan.side is Side.LONG else OrderSide.SELL
            order_list = self.order_factory.bracket(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=quantity,
                entry_order_type=OrderType.LIMIT,
                entry_price=self.instrument.make_price(entry),
                time_in_force=TimeInForce.GTC,
                tp_price=self.instrument.make_price(target),
                sl_trigger_price=self.instrument.make_price(stop),
            )
            self.active_owner = plan.scenario_id
            self.pending_instruction = instruction
            self.pending_submission_ns = ts_ns
            self.pending_entry_price = entry
            self.pending_stop_price = stop
            self.pending_target_price = target
            self.submit_order_list(order_list)
            effective_leverage = quantity_value * entry / equity
            submission = {
                **asdict(plan),
                "side": plan.side.value,
                "response": plan.response,
                "submission_time_ns": ts_ns,
                "submission_trade_id": str(tick.trade_id),
                "market_price_at_submission": current,
                "entry_order_type": "LIMIT",
                "planned_entry_price": entry,
                "entry_expiry_time_ns": instruction.expiry_time_ns,
                "entry_reason": instruction.entry_reason,
                "rounded_stop_price": stop,
                "rounded_target_price": target,
                "quantity": quantity_value,
                "engine_equity_at_submission": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_after_cost": planned_loss,
                "price_risk_fraction": price_fraction,
                "net_reward_risk_at_submission": net_rr,
                "effective_leverage_at_submission": effective_leverage,
                "signal_to_submission_ns": ts_ns - int(plan.signal_time_ns),
            }
            self.submissions.append(submission)
            self._record("LIMIT_BRACKET_SUBMITTED", ts_ns, **submission)

        def _collect_due(self, ts_ns: int) -> list[RestingEntryInstruction]:
            due: list[RestingEntryInstruction] = []
            while self.plan_cursor < len(self.instructions):
                instruction = self.instructions[self.plan_cursor]
                if int(instruction.plan.signal_time_ns) >= ts_ns:
                    break
                self.plan_cursor += 1
                due.append(instruction)
            return due

        def on_trade_tick(self, tick: TradeTick) -> None:
            ts_ns = int(tick.ts_event)
            self._last_ts_ns = ts_ns
            self._mark_nav_if_needed(ts_ns)

            if ts_ns >= self.config.evaluation_end_ns:
                if not self.end_exit_submitted:
                    self.end_exit_submitted = True
                    if self.pending_instruction is not None:
                        self.pending_entries_canceled_at_end += 1
                    self._flatten(
                        ts_ns=ts_ns,
                        reason="EVALUATION_END_EXIT_SUBMITTED",
                    )
                return

            self._time_exit_if_needed(ts_ns)
            if self._manage_pending(tick):
                return
            due = self._collect_due(ts_ns)
            self._submit_due(tick, due)

        def on_position_opened(self, event: PositionOpened) -> None:
            self.position_opened_ns = int(event.ts_event)
            self._clear_pending()
            self._record("POSITION_OPENED", int(event.ts_event), event=str(event))
            self._mark_nav(int(event.ts_event))

        def on_position_closed(self, event: PositionClosed) -> None:
            self.position_opened_ns = None
            self.cancel_all_orders(self.config.instrument_id)
            self._clear_pending()
            self._record("POSITION_CLOSED", int(event.ts_event), event=str(event))
            self._release_gate()
            self._mark_nav(int(event.ts_event))

        def _handle_order_failure(self, event_type: str, event: Any) -> None:
            ts_ns = int(event.ts_event)
            self._record(event_type, ts_ns, event=str(event))
            if self.portfolio.is_flat(self.config.instrument_id):
                self._clear_pending()
                self._release_gate()
                return
            self.protective_order_failures += 1
            self._flatten(
                ts_ns=ts_ns,
                reason="PROTECTIVE_ORDER_FAILURE_EXIT_SUBMITTED",
            )

        def on_order_denied(self, event: Any) -> None:
            self._handle_order_failure("ORDER_DENIED", event)

        def on_order_rejected(self, event: Any) -> None:
            self._handle_order_failure("ORDER_REJECTED", event)

        def on_stop(self) -> None:
            self.cancel_all_orders(self.config.instrument_id)
            self._clear_pending()
            if not self.portfolio.is_flat(self.config.instrument_id):
                self.close_all_positions(
                    self.config.instrument_id,
                    reduce_only=True,
                )
            self._release_gate()

        def finalize(self) -> None:
            if self._last_ts_ns >= self.config.evaluation_start_ns:
                self._mark_nav(self._last_ts_ns)
            if self._current_day is not None and self._last_day_equity is not None:
                if not self.daily_nav or self.daily_nav[-1]["date"] != self._current_day:
                    self.daily_nav.append(
                        {"date": self._current_day, "nav": self._last_day_equity},
                    )
            self.ended_flat = self.portfolio.is_flat(self.config.instrument_id)

    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    btc = Currency.from_str("BTC")
    cost_fraction = Decimal(
        str(execution.all_in_cost_bps_per_side / 10_000.0),
    )
    instrument = CryptoPerpetual(
        instrument_id=InstrumentId(Symbol("BTCUSDT-PERP"), venue),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=btc,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=execution.price_precision,
        size_precision=execution.quantity_precision,
        price_increment=Price.from_str(
            f"{execution.price_increment:.{execution.price_precision}f}",
        ),
        size_increment=Quantity.from_str(
            f"{execution.quantity_increment:.{execution.quantity_precision}f}",
        ),
        ts_event=0,
        ts_init=0,
        min_quantity=Quantity.from_str(
            f"{execution.quantity_increment:.{execution.quantity_precision}f}",
        ),
        min_notional=Money(10.0, usdt),
        max_price=Price.from_str(
            f"{10_000_000:.{execution.price_precision}f}",
        ),
        min_price=Price.from_str(
            f"{execution.price_increment:.{execution.price_precision}f}",
        ),
        margin_init=Decimal(1) / Decimal(str(execution.venue_max_leverage)),
        margin_maint=(
            Decimal(1) / Decimal(str(execution.venue_max_leverage))
        ) / Decimal(2),
        maker_fee=cost_fraction,
        taker_fee=cost_fraction,
        info={
            "execution_engine": "NautilusTrader",
            "market_data": "official Binance Vision USD-M aggregate trades",
            "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
            "entry_order_type": "LIMIT",
        },
    )

    ticks: list[TradeTick] = []
    skipped_zero_precision = 0
    for trade in ordered_trades:
        quantity = instrument.make_qty(trade.quantity, round_down=True)
        if _as_float(quantity) <= 0.0:
            skipped_zero_precision += 1
            continue
        ticks.append(
            TradeTick(
                instrument_id=instrument.id,
                price=instrument.make_price(trade.price),
                size=quantity,
                aggressor_side=(
                    AggressorSide.SELLER
                    if trade.is_buyer_maker
                    else AggressorSide.BUYER
                ),
                trade_id=TradeId(str(trade.agg_trade_id)),
                ts_event=int(trade.ts_event_ns),
                ts_init=int(trade.ts_event_ns),
            ),
        )
    if not ticks:
        raise ValueError("no positive-precision TradeTick objects were produced")

    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
    )
    gate = GlobalEntryGate()
    strategy = RestingPlanStrategy(
        RestingPlanStrategyConfig(
            instrument_id=instrument.id,
            risk_fraction=Decimal(str(execution.risk_fraction)),
            cost_fraction_per_side=cost_fraction,
            minimum_net_reward_risk=Decimal(
                str(execution.minimum_net_reward_risk),
            ),
            minimum_price_risk_fraction=Decimal(
                str(execution.minimum_price_risk_fraction),
            ),
            evaluation_start_ns=start_ns,
            evaluation_end_ns=end_ns,
            maximum_hold_ns=maximum_hold_ns,
        ),
        instruction_sequence=eligible,
        gate=gate,
        instrument=instrument,
    )

    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(execution.starting_nav, usdt)],
            base_currency=usdt,
            default_leverage=Decimal(str(execution.venue_max_leverage)),
            reject_stop_orders=False,
            trade_execution=True,
        )
        engine.add_instrument(instrument)
        engine.add_data(ticks)
        engine.add_strategy(strategy)
        engine.run()
        strategy.finalize()

        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(venue)
        metrics = _build_metrics(
            label=label,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            execution=execution,
            strategy=strategy,
            fills=fills,
            positions=positions,
        )
        metrics.update(
            {
                "market_data_for_execution": (
                    "official Binance Vision USD-M aggregate trades as "
                    "NautilusTrader TradeTick"
                ),
                "entry_semantics": (
                    "limit bracket armed on first venue trade strictly after "
                    "completed signal; entry rests at causal confirmation "
                    "boundary and is matched only by later NautilusTrader "
                    "trade-tick processing"
                ),
                "entry_order_type": "LIMIT",
                "trade_execution": True,
                "execution_trade_ticks": len(ticks),
                "evaluation_trade_ticks": evaluation_count,
                "post_evaluation_flush_ticks": post_end_count,
                "skipped_zero_precision_trade_ticks": skipped_zero_precision,
                "bar_adaptive_high_low_ordering": None,
                "limit_entries_expired": strategy.limit_entries_expired,
                "targets_consumed_before_entry": (
                    strategy.targets_consumed_before_entry
                ),
                "pending_entries_canceled_at_end": (
                    strategy.pending_entries_canceled_at_end
                ),
                "ended_without_pending_entry": (
                    strategy.pending_instruction is None
                ),
            },
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        fills.to_csv(output_dir / "orders.csv", index=False)
        positions.to_csv(output_dir / "positions.csv", index=False)
        account.to_csv(output_dir / "account.csv", index=False)
        pd.DataFrame(strategy.submissions).to_csv(
            output_dir / "trade_plans.csv",
            index=False,
        )
        pd.DataFrame(strategy.rejections).to_csv(
            output_dir / "rejections.csv",
            index=False,
        )
        pd.DataFrame(strategy.daily_nav).to_csv(
            output_dir / "daily_nav.csv",
            index=False,
        )
        _write_jsonl(
            output_dir / "execution_events.jsonl",
            strategy.execution_events,
        )
        _atomic_json(output_dir / "nautilus_metrics.json", metrics)
        _atomic_json(
            output_dir / "execution_contract.json",
            {
                "authoritative_performance_engine": "NautilusTrader",
                "nautilus_trader_version": package_version("nautilus_trader"),
                "custom_fill_simulator": False,
                "custom_pnl_or_nav_ledger": False,
                "signal_generation_is_candidate_logic": True,
                "order_matching_commission_margin_positions_accounting": (
                    "NautilusTrader"
                ),
                "execution_market_data": (
                    "official Binance Vision USD-M aggregate trades converted "
                    "one-for-one to NautilusTrader TradeTick"
                ),
                "entry_order_type": "LIMIT",
                "entry_delay": (
                    "arm on first venue trade strictly after signal; fill only "
                    "on later venue trade at confirmation boundary or better"
                ),
                "risk_budget": (
                    "current NautilusTrader portfolio equity * fixed 3%"
                ),
                "one_global_pending_or_open_position": True,
            },
        )
        return NautilusRunEvidence(
            metrics=metrics,
            daily_nav=list(strategy.daily_nav),
            submissions=list(strategy.submissions),
            rejections=list(strategy.rejections),
            execution_events=list(strategy.execution_events),
            fills=fills,
            positions=positions,
            account=account,
        )
    finally:
        engine.dispose()
