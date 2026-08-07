"""Authoritative multi-instrument NautilusTrader TradeTick execution.

Candidate logic supplies completed plans tagged with one of BTCUSDT, ETHUSDT,
SOLUSDT or XRPUSDT. Official Binance Vision USD-M aggregate trades are
converted one-for-one to NautilusTrader TradeTick objects and merged in event
order. One NautilusTrader account and one global entry gate enforce that the
sum of pending new entries and open positions never exceeds one.

This module contains no custom fill, stop/target, PnL or NAV simulator.
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
class InstrumentSpec:
    symbol: str
    base_currency: str
    price_increment: str
    quantity_increment: str
    min_quantity: str
    min_notional: float
    min_price: str
    max_price: str

    def __post_init__(self) -> None:
        if not self.symbol.endswith("USDT"):
            raise ValueError(f"unsupported quote currency for {self.symbol}")
        for name in (
            "price_increment",
            "quantity_increment",
            "min_quantity",
            "min_price",
            "max_price",
        ):
            if Decimal(getattr(self, name)) <= 0:
                raise ValueError(f"{self.symbol} {name} must be positive")
        if self.min_notional <= 0.0:
            raise ValueError(f"{self.symbol} min_notional must be positive")

    @staticmethod
    def _precision(value: str) -> int:
        normalized = format(Decimal(value).normalize(), "f")
        return len(normalized.partition(".")[2])

    @property
    def price_precision(self) -> int:
        return self._precision(self.price_increment)

    @property
    def quantity_precision(self) -> int:
        return self._precision(self.quantity_increment)


@dataclass(frozen=True, slots=True)
class SymbolScenarioPlan:
    symbol: str
    plan: ScenarioPlan

    @property
    def owner(self) -> str:
        return f"{self.symbol}:{self.plan.scenario_id}"


def run_nautilus_multi_tick_plan_backtest(
    *,
    label: str,
    trades_by_symbol: Mapping[str, Sequence[AggTrade]],
    plans: Sequence[SymbolScenarioPlan],
    instrument_specs: Mapping[str, InstrumentSpec],
    evaluation_start: datetime,
    evaluation_end: datetime,
    execution: NautilusExecutionConfig,
    maximum_hold_ns: int,
    output_dir: Path,
) -> NautilusRunEvidence:
    """Execute a single-account multi-symbol portfolio only in NautilusTrader."""

    if evaluation_end <= evaluation_start:
        raise ValueError("evaluation_end must be after evaluation_start")
    if maximum_hold_ns <= 0:
        raise ValueError("maximum_hold_ns must be positive")
    symbols = tuple(sorted(trades_by_symbol))
    if not symbols:
        raise ValueError("trades_by_symbol cannot be empty")
    if set(symbols) != set(instrument_specs):
        raise ValueError("trade symbols and instrument specs must match exactly")
    unknown_plan_symbols = sorted({item.symbol for item in plans} - set(symbols))
    if unknown_plan_symbols:
        raise ValueError(f"plans contain unknown symbols: {unknown_plan_symbols}")

    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
    from nautilus_trader.model.data import TradeTick
    from nautilus_trader.model.enums import (
        AccountType,
        AggressorSide,
        OmsType,
        OrderSide,
        TimeInForce,
    )
    from nautilus_trader.model.events import PositionClosed, PositionOpened
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, TradeId, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity
    from nautilus_trader.trading.strategy import Strategy

    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)
    counts: dict[str, dict[str, int]] = {}
    ordered_by_symbol: dict[str, list[AggTrade]] = {}
    for symbol in symbols:
        ordered = sorted(
            trades_by_symbol[symbol],
            key=lambda item: (item.ts_event_ns, item.agg_trade_id),
        )
        if not ordered:
            raise ValueError(f"{symbol} execution trades cannot be empty")
        prior_time = -1
        prior_id = -1
        evaluation_count = 0
        post_end_count = 0
        for trade in ordered:
            if trade.ts_event_ns < prior_time:
                raise ValueError(f"{symbol} execution timestamp regression")
            if trade.agg_trade_id <= prior_id:
                raise ValueError(f"{symbol} aggregate-trade ID regression")
            prior_time = trade.ts_event_ns
            prior_id = trade.agg_trade_id
            if start_ns <= trade.ts_event_ns < end_ns:
                evaluation_count += 1
            elif trade.ts_event_ns >= end_ns:
                post_end_count += 1
        if evaluation_count <= 0:
            raise ValueError(f"{symbol} has no evaluation TradeTicks")
        if post_end_count < 3:
            raise ValueError(f"{symbol} requires three post-evaluation ticks")
        counts[symbol] = {
            "evaluation": evaluation_count,
            "post_evaluation": post_end_count,
        }
        ordered_by_symbol[symbol] = ordered

    eligible: dict[str, list[SymbolScenarioPlan]] = {symbol: [] for symbol in symbols}
    for item in sorted(
        plans,
        key=lambda row: (row.plan.signal_time_ns, row.symbol, row.plan.scenario_id),
    ):
        if start_ns <= int(item.plan.signal_time_ns) < end_ns:
            eligible[item.symbol].append(item)

    class MultiTickPlanStrategyConfig(StrategyConfig, frozen=True):
        instrument_ids: tuple[InstrumentId, ...]
        risk_fraction: Decimal
        cost_fraction_per_side: Decimal
        minimum_net_reward_risk: Decimal
        minimum_price_risk_fraction: Decimal
        evaluation_start_ns: int
        evaluation_end_ns: int
        maximum_hold_ns: int

    class MultiTickPlanStrategy(Strategy):
        def __init__(
            self,
            config: MultiTickPlanStrategyConfig,
            *,
            plans_by_symbol: Mapping[str, Sequence[SymbolScenarioPlan]],
            gate: GlobalEntryGate,
            instruments: Mapping[str, CryptoPerpetual],
        ) -> None:
            super().__init__(config)
            self.plans_by_symbol = plans_by_symbol
            self.cursors = {symbol: 0 for symbol in plans_by_symbol}
            self.gate = gate
            self.instruments = instruments
            self.symbol_by_instrument_id = {
                instrument.id: symbol for symbol, instrument in instruments.items()
            }
            self.active_owner: str | None = None
            self.active_symbol: str | None = None
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

        def on_start(self) -> None:
            for instrument_id in self.config.instrument_ids:
                self.subscribe_trade_ticks(instrument_id)

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
            item: SymbolScenarioPlan,
            *,
            ts_ns: int,
            reason: str,
            **details: Any,
        ) -> None:
            plan = item.plan
            row = {
                "symbol": item.symbol,
                "scenario_id": plan.scenario_id,
                "signal_time_ns": plan.signal_time_ns,
                "entry_evaluation_time_ns": ts_ns,
                "side": plan.side.value,
                "response": plan.response,
                "reason": reason,
                **details,
            }
            self.rejections.append(row)
            self._record("PLAN_REJECTED", ts_ns, **row)

        def _equity(self) -> float:
            first = next(iter(self.instruments.values()))
            equity = self.portfolio.equity(first.id.venue)
            return _money_from_equity_map(equity, Currency.from_str("USDT"))

        def _all_flat(self) -> bool:
            return all(
                self.portfolio.is_flat(instrument.id)
                for instrument in self.instruments.values()
            )

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
                self.daily_nav.append({"date": self._current_day, "nav": self._last_day_equity})
                self._current_day = day
            self._last_day_equity = equity
            self.final_nav = equity
            if self._high_water is None or equity > self._high_water:
                self._high_water = equity
            if self._high_water > 0.0:
                self.max_drawdown = min(self.max_drawdown, equity / self._high_water - 1.0)
            first = next(iter(self.instruments.values()))
            margins = self.portfolio.margins_maint(first.id.venue) or {}
            try:
                maintenance = _money_from_equity_map(
                    margins,
                    Currency.from_str("USDT"),
                )
            except RuntimeError:
                maintenance = 0.0
            if maintenance > 0.0:
                ratio = equity / maintenance
                self.minimum_equity_to_maintenance_margin = (
                    ratio
                    if self.minimum_equity_to_maintenance_margin is None
                    else min(self.minimum_equity_to_maintenance_margin, ratio)
                )

        def _mark_nav_if_needed(self, ts_ns: int) -> None:
            effective_ts = min(ts_ns, self.config.evaluation_end_ns - 1)
            day = _utc_date(effective_ts)
            if (
                self.start_nav is None
                or day != self._current_day
                or not self._all_flat()
                or ts_ns >= self.config.evaluation_end_ns
            ):
                self._mark_nav(ts_ns)

        def _release_gate(self) -> None:
            if self.active_owner is not None:
                self.gate.release(self.active_owner)
            self.active_owner = None
            self.active_symbol = None

        def _flatten(self, *, ts_ns: int, reason: str) -> None:
            symbol = self.active_symbol
            if symbol is None:
                if self._all_flat():
                    self._release_gate()
                return
            instrument_id = self.instruments[symbol].id
            self.cancel_all_orders(instrument_id)
            if not self.portfolio.is_flat(instrument_id):
                self.close_all_positions(instrument_id, reduce_only=True)
                self._record(reason, ts_ns, symbol=symbol)
            elif self._all_flat():
                self._release_gate()

        def _time_exit_if_needed(self, ts_ns: int) -> None:
            if self.position_opened_ns is None:
                return
            if ts_ns - self.position_opened_ns < self.config.maximum_hold_ns:
                return
            self.maximum_hold_exits += 1
            self._flatten(ts_ns=ts_ns, reason="MAXIMUM_HOLD_EXIT_SUBMITTED")
            self.position_opened_ns = None

        def _viable(
            self,
            item: SymbolScenarioPlan,
            *,
            entry: float,
            ts_ns: int,
        ) -> tuple[float, float, float, float, float] | None:
            plan = item.plan
            instrument = self.instruments[item.symbol]
            stop = _as_float(instrument.make_price(plan.stop_price))
            target = _as_float(instrument.make_price(plan.target_price))
            hold = _as_float(instrument.make_price(plan.confirmation_hold_price))
            hold_ok = entry >= hold if plan.side is Side.LONG else entry <= hold
            if not hold_ok:
                self._reject(
                    item,
                    ts_ns=ts_ns,
                    reason="FAILED_CONFIRMATION_HOLD",
                    entry=entry,
                    hold=hold,
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
                    item,
                    ts_ns=ts_ns,
                    reason="INVALID_TICK_GEOMETRY",
                    entry=entry,
                    stop=stop,
                    target=target,
                )
                return None
            cost = float(self.config.cost_fraction_per_side)
            price_risk = abs(entry - stop)
            planned_loss = price_risk + entry * cost + stop * cost
            planned_gain = abs(target - entry) - entry * cost - target * cost
            price_fraction = price_risk / planned_loss if planned_loss > 0.0 else 0.0
            net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
            if price_fraction < float(self.config.minimum_price_risk_fraction):
                self._reject(
                    item,
                    ts_ns=ts_ns,
                    reason="COST_DOMINATED",
                    entry=entry,
                    stop=stop,
                    target=target,
                    price_risk_fraction=price_fraction,
                    net_reward_risk=net_rr,
                )
                return None
            if planned_gain <= 0.0 or net_rr < float(self.config.minimum_net_reward_risk):
                self._reject(
                    item,
                    ts_ns=ts_ns,
                    reason="INSUFFICIENT_NET_REWARD_RISK",
                    entry=entry,
                    stop=stop,
                    target=target,
                    price_risk_fraction=price_fraction,
                    net_reward_risk=net_rr,
                )
                return None
            return stop, target, planned_loss, price_fraction, net_rr

        def _submit_due(
            self,
            tick: TradeTick,
            symbol: str,
            due: Sequence[SymbolScenarioPlan],
        ) -> None:
            if not due:
                return
            ts_ns = int(tick.ts_event)
            if not self._all_flat() or self.gate.owner is not None:
                for item in due:
                    self._reject(
                        item,
                        ts_ns=ts_ns,
                        reason="GLOBAL_PENDING_ENTRY_OR_POSITION_OCCUPIED",
                        gate_owner=self.gate.owner,
                    )
                return
            instrument = self.instruments[symbol]
            entry = _as_float(tick.price)
            viable: list[
                tuple[float, SymbolScenarioPlan, float, float, float, float]
            ] = []
            for item in due:
                geometry = self._viable(item, entry=entry, ts_ns=ts_ns)
                if geometry is None:
                    continue
                stop, target, planned_loss, price_fraction, net_rr = geometry
                viable.append(
                    (net_rr, item, stop, target, planned_loss, price_fraction),
                )
            if not viable:
                return
            ordered = sorted(
                viable,
                key=lambda row: (-row[0], row[1].owner),
            )
            net_rr, item, stop, target, planned_loss, price_fraction = ordered[0]
            for _, competing, *_ in ordered[1:]:
                self._reject(
                    competing,
                    ts_ns=ts_ns,
                    reason="LOWER_NET_RR_COMPETING_SAME_SYMBOL_PLAN",
                )
            if not self.gate.acquire(item.owner):
                self.gate_violations += 1
                self._reject(item, ts_ns=ts_ns, reason="GLOBAL_ENTRY_GATE_ACQUIRE_FAILED")
                return

            plan = item.plan
            equity = self._equity()
            risk_budget = equity * float(self.config.risk_fraction)
            raw_quantity = risk_budget / planned_loss
            quantity = instrument.make_qty(raw_quantity, round_down=True)
            quantity_value = _as_float(quantity)
            if quantity_value <= 0.0:
                self.gate.release(item.owner)
                self._reject(item, ts_ns=ts_ns, reason="ZERO_QUANTITY_AFTER_PRECISION")
                return
            side = OrderSide.BUY if plan.side is Side.LONG else OrderSide.SELL
            order_list = self.order_factory.bracket(
                instrument_id=instrument.id,
                order_side=side,
                quantity=quantity,
                time_in_force=TimeInForce.GTC,
                tp_price=instrument.make_price(target),
                sl_trigger_price=instrument.make_price(stop),
            )
            self.active_owner = item.owner
            self.active_symbol = symbol
            self.submit_order_list(order_list)
            effective_leverage = quantity_value * entry / equity
            submission = {
                **asdict(plan),
                "symbol": symbol,
                "side": plan.side.value,
                "response": plan.response,
                "submission_time_ns": ts_ns,
                "submission_trade_id": str(tick.trade_id),
                "entry_reference": entry,
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
            self._record("BRACKET_SUBMITTED", ts_ns, **submission)

        def _collect_due(self, symbol: str, ts_ns: int) -> list[SymbolScenarioPlan]:
            sequence = self.plans_by_symbol[symbol]
            cursor = self.cursors[symbol]
            due: list[SymbolScenarioPlan] = []
            while cursor < len(sequence):
                item = sequence[cursor]
                if int(item.plan.signal_time_ns) >= ts_ns:
                    break
                cursor += 1
                due.append(item)
            self.cursors[symbol] = cursor
            return due

        def on_trade_tick(self, tick: TradeTick) -> None:
            ts_ns = int(tick.ts_event)
            self._last_ts_ns = ts_ns
            self._mark_nav_if_needed(ts_ns)
            if ts_ns >= self.config.evaluation_end_ns:
                if not self.end_exit_submitted:
                    self.end_exit_submitted = True
                    self._flatten(ts_ns=ts_ns, reason="EVALUATION_END_EXIT_SUBMITTED")
                return
            self._time_exit_if_needed(ts_ns)
            symbol = self.symbol_by_instrument_id[tick.instrument_id]
            self._submit_due(tick, symbol, self._collect_due(symbol, ts_ns))

        def on_position_opened(self, event: PositionOpened) -> None:
            self.position_opened_ns = int(event.ts_event)
            self._record(
                "POSITION_OPENED",
                int(event.ts_event),
                symbol=self.active_symbol,
                event=str(event),
            )
            self._mark_nav(int(event.ts_event))

        def on_position_closed(self, event: PositionClosed) -> None:
            symbol = self.active_symbol
            self.position_opened_ns = None
            if symbol is not None:
                self.cancel_all_orders(self.instruments[symbol].id)
            self._record(
                "POSITION_CLOSED",
                int(event.ts_event),
                symbol=symbol,
                event=str(event),
            )
            self._release_gate()
            self._mark_nav(int(event.ts_event))

        def _handle_order_failure(self, event_type: str, event: Any) -> None:
            ts_ns = int(event.ts_event)
            self._record(event_type, ts_ns, symbol=self.active_symbol, event=str(event))
            if self._all_flat():
                self._release_gate()
                return
            self.protective_order_failures += 1
            self._flatten(ts_ns=ts_ns, reason="PROTECTIVE_ORDER_FAILURE_EXIT_SUBMITTED")

        def on_order_denied(self, event: Any) -> None:
            self._handle_order_failure("ORDER_DENIED", event)

        def on_order_rejected(self, event: Any) -> None:
            self._handle_order_failure("ORDER_REJECTED", event)

        def on_stop(self) -> None:
            self._flatten(ts_ns=self._last_ts_ns, reason="STRATEGY_STOP_EXIT_SUBMITTED")
            self._release_gate()

        def finalize(self) -> None:
            if self._last_ts_ns >= self.config.evaluation_start_ns:
                self._mark_nav(self._last_ts_ns)
            if self._current_day is not None and self._last_day_equity is not None:
                if not self.daily_nav or self.daily_nav[-1]["date"] != self._current_day:
                    self.daily_nav.append({"date": self._current_day, "nav": self._last_day_equity})
            self.ended_flat = self._all_flat()

    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    cost_fraction = Decimal(str(execution.all_in_cost_bps_per_side / 10_000.0))
    instruments: dict[str, CryptoPerpetual] = {}
    for symbol in symbols:
        spec = instrument_specs[symbol]
        base = Currency.from_str(spec.base_currency)
        instruments[symbol] = CryptoPerpetual(
            instrument_id=InstrumentId(Symbol(f"{symbol}-PERP"), venue),
            raw_symbol=Symbol(symbol),
            base_currency=base,
            quote_currency=usdt,
            settlement_currency=usdt,
            is_inverse=False,
            price_precision=spec.price_precision,
            size_precision=spec.quantity_precision,
            price_increment=Price.from_str(spec.price_increment),
            size_increment=Quantity.from_str(spec.quantity_increment),
            ts_event=0,
            ts_init=0,
            min_quantity=Quantity.from_str(spec.min_quantity),
            min_notional=Money(spec.min_notional, usdt),
            max_price=Price.from_str(spec.max_price),
            min_price=Price.from_str(spec.min_price),
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
                "price_increment": spec.price_increment,
                "quantity_increment": spec.quantity_increment,
            },
        )

    ticks: list[TradeTick] = []
    skipped_zero_precision: dict[str, int] = {symbol: 0 for symbol in symbols}
    merged: list[tuple[int, str, AggTrade]] = []
    for symbol in symbols:
        merged.extend(
            (int(trade.ts_event_ns), symbol, trade)
            for trade in ordered_by_symbol[symbol]
        )
    merged.sort(key=lambda row: (row[0], row[1], row[2].agg_trade_id))
    for _, symbol, trade in merged:
        instrument = instruments[symbol]
        quantity = instrument.make_qty(trade.quantity, round_down=True)
        if _as_float(quantity) <= 0.0:
            skipped_zero_precision[symbol] += 1
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
                trade_id=TradeId(f"{symbol}-{trade.agg_trade_id}"),
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
    strategy = MultiTickPlanStrategy(
        MultiTickPlanStrategyConfig(
            instrument_ids=tuple(instruments[symbol].id for symbol in symbols),
            risk_fraction=Decimal(str(execution.risk_fraction)),
            cost_fraction_per_side=cost_fraction,
            minimum_net_reward_risk=Decimal(str(execution.minimum_net_reward_risk)),
            minimum_price_risk_fraction=Decimal(
                str(execution.minimum_price_risk_fraction),
            ),
            evaluation_start_ns=start_ns,
            evaluation_end_ns=end_ns,
            maximum_hold_ns=maximum_hold_ns,
        ),
        plans_by_symbol=eligible,
        gate=gate,
        instruments=instruments,
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
        for symbol in symbols:
            engine.add_instrument(instruments[symbol])
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
        submission_symbol_counts = (
            pd.Series([row["symbol"] for row in strategy.submissions]).value_counts().to_dict()
            if strategy.submissions
            else {}
        )
        metrics.update(
            {
                "market_data_for_execution": (
                    "official Binance Vision USD-M aggregate trades as globally "
                    "ordered NautilusTrader TradeTick streams"
                ),
                "entry_semantics": (
                    "each plan is eligible only on the first strictly later "
                    "TradeTick of its own symbol; exact timestamp ties use "
                    "lexical symbol order"
                ),
                "trade_execution": True,
                "symbols": list(symbols),
                "execution_trade_ticks": len(ticks),
                "evaluation_trade_ticks_by_symbol": {
                    symbol: counts[symbol]["evaluation"] for symbol in symbols
                },
                "post_evaluation_flush_ticks_by_symbol": {
                    symbol: counts[symbol]["post_evaluation"] for symbol in symbols
                },
                "skipped_zero_precision_trade_ticks_by_symbol": skipped_zero_precision,
                "submission_symbol_counts": submission_symbol_counts,
                "bar_adaptive_high_low_ordering": None,
            },
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        fills.to_csv(output_dir / "orders.csv", index=False)
        positions.to_csv(output_dir / "positions.csv", index=False)
        account.to_csv(output_dir / "account.csv", index=False)
        pd.DataFrame(strategy.submissions).to_csv(output_dir / "trade_plans.csv", index=False)
        pd.DataFrame(strategy.rejections).to_csv(output_dir / "rejections.csv", index=False)
        pd.DataFrame(strategy.daily_nav).to_csv(output_dir / "daily_nav.csv", index=False)
        _write_jsonl(output_dir / "execution_events.jsonl", strategy.execution_events)
        _atomic_json(output_dir / "nautilus_metrics.json", metrics)
        _atomic_json(
            output_dir / "execution_contract.json",
            {
                "authoritative_performance_engine": "NautilusTrader",
                "nautilus_trader_version": package_version("nautilus_trader"),
                "custom_fill_simulator": False,
                "custom_pnl_or_nav_ledger": False,
                "single_shared_account": True,
                "one_global_pending_or_open_position": True,
                "risk_budget": "current shared Nautilus portfolio equity * fixed 3%",
                "order_matching_commission_margin_positions_accounting": "NautilusTrader",
                "instrument_specs": {
                    symbol: asdict(instrument_specs[symbol]) for symbol in symbols
                },
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


__all__ = [
    "InstrumentSpec",
    "SymbolScenarioPlan",
    "run_nautilus_multi_tick_plan_backtest",
]
