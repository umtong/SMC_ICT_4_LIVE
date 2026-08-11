"""NautilusTrader strategy for the EasyChart v2 state engine.

NautilusTrader owns bar aggregation, order contingencies, matching, fills,
positions, fees and account balances.  This strategy only chooses one plan and
sizes the bracket from current NAV.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.events import OrderCanceled, OrderDenied, OrderExpired, OrderRejected, PositionClosed
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId, Venue
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.orders.list import OrderList
from nautilus_trader.trading.strategy import Strategy

from model import Candle, EasyChartStateEngine, EngineConfig, Side, TradePlan


class EasyChartV2Config(StrategyConfig, frozen=True):
    instrument_ids: tuple[InstrumentId, ...]
    signal_bar_types: tuple[BarType, ...]
    execution_bar_types: tuple[BarType, ...]
    risk_fraction: float = 0.03
    min_gross_rr: float = 1.0
    pivot_spans: tuple[int, ...] = (2, 6, 12)
    min_prominence_atr: float = 1.0
    estimated_entry_fee_rate: float = 0.00075
    estimated_stop_fee_rate: float = 0.00075
    estimated_funding_rate: float = 0.00010
    enable_rejection: bool = True
    enable_acceptance: bool = True
    trading_start_ns: int = 0


class EasyChartV2Strategy(Strategy):
    def __init__(self, config: EasyChartV2Config) -> None:
        super().__init__(config)
        count = len(config.instrument_ids)
        if len(config.signal_bar_types) != count or len(config.execution_bar_types) != count:
            raise ValueError("instrument_ids and both bar type tuples must have equal length")

        self.instruments: dict[InstrumentId, Any] = {}
        self.engines: dict[InstrumentId, EasyChartStateEngine] = {}
        self.signal_to_instrument = dict(
            zip(config.signal_bar_types, config.instrument_ids, strict=True),
        )
        self.execution_to_instrument = dict(
            zip(config.execution_bar_types, config.instrument_ids, strict=True),
        )

        # One global order intent or position across the four-symbol universe.
        self.active_plan: TradePlan | None = None
        self.active_instrument_id: InstrumentId | None = None
        self.active_entry_id: ClientOrderId | None = None
        self.entry_cancel_requested = False

        # Buffer same-timestamp signal bars so arbitration is independent of
        # the order in which instruments happen to arrive from the data engine.
        self.signal_bucket_ts: int | None = None
        self.signal_bucket_seen: set[InstrumentId] = set()
        self.signal_bucket_plans: list[tuple[InstrumentId, TradePlan]] = []

        self.event_log: list[dict[str, Any]] = []
        self.plan_log: dict[str, TradePlan] = {}

    def on_start(self) -> None:
        for instrument_id, signal_type, execution_type in zip(
            self.config.instrument_ids,
            self.config.signal_bar_types,
            self.config.execution_bar_types,
            strict=True,
        ):
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                self.log.error(f"Could not find instrument for {instrument_id}")
                self.stop()
                return
            self.instruments[instrument_id] = instrument
            self.engines[instrument_id] = EasyChartStateEngine(
                symbol=instrument.raw_symbol.value,
                config=EngineConfig(
                    pivot_spans=self.config.pivot_spans,
                    min_prominence_atr=self.config.min_prominence_atr,
                    min_gross_rr=self.config.min_gross_rr,
                    tick_size=float(instrument.price_increment),
                    enable_rejection=self.config.enable_rejection,
                    enable_acceptance=self.config.enable_acceptance,
                ),
            )
            # The source 1m bars move the simulated exchange and allow pending
            # intent cancellation. Nautilus aggregates the 5m composite bars.
            self.subscribe_bars(execution_type)
            self.subscribe_bars(signal_type)

    def _record(self, kind: str, **values: Any) -> None:
        self.event_log.append({"kind": kind, "ts_ns": self.clock.timestamp_ns(), **values})

    def _portfolio_flat(self) -> bool:
        return all(self.portfolio.is_flat(instrument_id) for instrument_id in self.config.instrument_ids)

    def _pending_plan_invalidated(self, bar: Bar) -> bool:
        if self.active_plan is None or self.active_instrument_id != bar.bar_type.instrument_id:
            return False
        if not self.portfolio.is_flat(self.active_instrument_id):
            return False
        plan = self.active_plan
        if plan.side is Side.LONG:
            return float(bar.low) <= plan.stop or float(bar.high) >= plan.target
        return float(bar.high) >= plan.stop or float(bar.low) <= plan.target

    def _cancel_spent_pending_plan(self, bar: Bar) -> None:
        if not self._pending_plan_invalidated(bar) or self.entry_cancel_requested:
            return
        plan = self.active_plan
        assert plan is not None
        self.entry_cancel_requested = True
        self.cancel_all_orders(self.active_instrument_id)
        self._record("pending_canceled_causal_end", plan_id=plan.plan_id)

    def on_bar(self, bar: Bar) -> None:
        # This is checked on every externally supplied 1m bar, not only on the
        # 5m signal bars, so an unfilled retest cannot survive after its stop or
        # target has already been consumed.
        self._cancel_spent_pending_plan(bar)

        instrument_id = self.signal_to_instrument.get(bar.bar_type)
        if instrument_id is None:
            return

        if self.signal_bucket_ts is None:
            self.signal_bucket_ts = bar.ts_event
        elif bar.ts_event != self.signal_bucket_ts:
            self._flush_signal_bucket()
            self.signal_bucket_ts = bar.ts_event

        state_engine = self.engines[instrument_ik]
        plans = state_engine.on_bar(
            Candle(
                ts_close_ns=bar.ts_event,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
            ),
        )
        self.signal_bucket_seen.add(instrument_id)

        if bar.ts_event >= self.config.trading_start_ns:
            for plan in plans:
                self.plan_log[plan.plan_id] = plan
                self.signal_bucket_plans.append((instrument_id, plan))
                self._record(
                    "plan",
                    plan_id=plan.plan_id,
                    causal_event_id=plan.causal_event_id,
                    symbol=plan.symbol,
                    family=plan.family.value,
                    side=plan.side.name,
                    entry=plan.entry,
                    stop=plan.stop,
                    target=plan.target,
                    gross_rr=plan.gross_rr,
                    source_span=plan.source_span,
                    source_prominence_atr=plan.source_prominence_atr,
                )

        if len(self.signal_bucket_seen) == len(self.config.instrument_ids):
            self._flush_signal_bucket()

    def _flush_signal_bucket(self) -> None:
        if (
            self.signal_bucket_plans
            and self.active_plan is None
            and self._portfolio_flat()
        ):
            instrument_id, chosen = sorted(
                self.signal_bucket_plans,
                key=lambda item: (
                    -item[1].source_span,
                    -item[1].source_prominence_atr,
                    item[1].symbol,
                    item[1].plan_id,
                ),
            )[0]
            self._submit_plan(instrument_id, chosen)
        self.signal_bucket_seen.clear()
        self.signal_bucket_plans.clear()
        self.signal_bucket_ts = None

    def _current_nav(self) -> Decimal:
        account = self.portfolio.account(Venue("BINANCE"))
        if account is None:
            raise RuntimeError("BINANCE account unavailable")
        money = account.balance_total(Currency.from_str("USDT"))
        if money is None:
            raise RuntimeError("USDT balance unavailable")
        return Decimal(str(money.as_double()))

    def _quantity(self, instrument: Any, plan]= TradePlan) -> Any | None:
        entry = Decimal(str(plan.entry))
        stop = Decimal(str(plan.stop))
        per_unit = abs(entry - stop)
        per_unit += entry * Decimal(str(self.config.estimated_entry_fee_rate))
        per_unit += stop * Decimal(str(self.config.estimated_stop_fee_rate))
        per_unit += entry * Decimal(str(self.config.estimated_funding_rate))
        if per_unit <= 0:
            return None
        raw = self._current_nav() * Decimal(str(self.config.risk_fraction)) / per_unit
        step = Decimal(str(instrument.size_increment))
        floored = (raw / step).to_integral_value(rounding=ROUND_DOWN) * step
        if floored <= 0 or floored < Decimal(str(instrument.min_quantity)):
            return None
        return instrument.make_qty(floored)

    def _submit_planself, instrument_id: InstrumentId, plan: TradePlan) -> None:
        instrument = self.instruments[instrument_id
        quantity = self._quantity(instrument, plan)
        if quantity is None:
            self._record("plan_rejected_quantity", plan_id=plan.plan_id)
            return
        order_side = OrderSide.BUY if plan.side is Side.LONG else OrderSide.SELL
        order_list: OrderList = self.order_factory.bracket(
            instrument_id=instrument_id,
            order_side=order_side,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            entry_price=instrument.make_price(plan.entry),
            sl_trigger_price=instrument.make_price(plan.stop),
            tp_price=instrument.make_price(plan.target),
            entry_order_type=OrderType.LIMIT,
        )
        self.active_plan = plan
        self.active_instrument_id = instrument_id
        self.active_entry_id = order_list.first.client_order_id
        self.entry_cancel_requested = False
        self.submit_order_list(order_list)
        self._record(
            "submitted",
            plan_id=plan.plan_id,
            instrument_id=str(instrument_id),
            quantity=str(quantity),
        )

    def _clear_if_entry(self, client_order_id: ClientOrderId) -> None:
        if self.active_entry_id is not None and client_order_id == self.active_entry_id:
            plan_id = self.active_plan.plan_id if self.active_plan else None
            self._record("entry_terminal_without_position", plan_id=plan_id)
            self.active_plan = None
            self.active_instrument_id = None
            self.active_entry_id = None
            self.entry_cancel_requested = False

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self._clear_if_entry(event.client_order_id)

    def on_order_expired(self, event: OrderExpired) -> None:
        self._clear_if_entry(event.client_order_id)

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._record("order_rejected", client_order_id=str(event.client_order_id), reason=str(event.reason))
        self._clear_if_entry(event.client_order_id)

    def on_order_denied(self, event: OrderDenied) -> None:
        self._record("order_denied", client_order_id=str(event.client_order_id), reason=str(event.reason))
        self._clear_if_entry(event.client_order_id)

    def on_position_closed(self, event: PositionClosed) -> None:
        plan_id = self.active_plan.plan_id if self.active_plan else None
        self._record("position_closed", plan_id=plan_id, instrument_id=str(event.instrument_id))
        self.active_plan = None
        self.active_instrument_id = None
        self.active_entry_id = None
        self.entry_cancel_requested = False

    def on_stop(self) -> None:
        for instrument_id, signal_type, execution_type in zip(
            self.config.instrument_ids,
            self.config.signal_bar_types,
            self.config.execution_bar_types,
            strict=True,
        ):
            self.cancel_all_orders(instrument_id)
            if not self.portfolio.is_flat(instrument_id):
                self.close_all_positions(instrument_id)
            self.unsubscribe_bars(signal_type)
            self.unsubscribe_bars(execution_type)
