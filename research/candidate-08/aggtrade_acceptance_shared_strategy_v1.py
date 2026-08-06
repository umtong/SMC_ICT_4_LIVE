"""Shared-account NautilusTrader adapter for causal acceptance-only signals."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from aggtrade_acceptance_causal_v1 import AcceptanceSignal
from logic import minutes_to_next_funding, risk_sized_quantity


class SharedAcceptanceStrategyConfig(StrategyConfig, frozen=True):
    instrument_ids: tuple[InstrumentId, ...]
    bar_types: tuple[BarType, ...]
    trading_start_ns: int
    trading_end_ns: int
    risk_fraction: Decimal
    effective_fee_rate: Decimal
    minimum_net_reward_risk: Decimal
    maximum_hold_ns: int
    funding_avoidance_minutes: int


class SharedAcceptanceStrategy(Strategy):
    """Allow at most one pending entry or open position over all four instruments."""

    def __init__(
        self,
        config: SharedAcceptanceStrategyConfig,
        signals_by_time_ns: dict[int, tuple[AcceptanceSignal, ...]],
    ) -> None:
        super().__init__(config=config)
        self.signals_by_time_ns = signals_by_time_ns
        self.instruments: dict[str, Any] = {}
        self.instrument_ids_by_symbol: dict[str, InstrumentId] = {}
        self.expected_instrument_ids = {str(item) for item in config.instrument_ids}
        self.usdt = Currency.from_str("USDT")

        self.current_timestamp_ns: int | None = None
        self.seen_instruments_at_timestamp: set[str] = set()
        self.processed_timestamps: set[int] = set()
        self.current_signal: AcceptanceSignal | None = None
        self.current_instrument_id: InstrumentId | None = None
        self.current_entry_order_id: str | None = None
        self.current_exit_order_ids: set[str] = set()
        self.position_open_ns: int | None = None
        self.entry_inflight = False
        self.exit_requested = False
        self.exit_request_reason: str | None = None
        self.last_fill_order_id: str | None = None

        self.trade_intents: list[dict[str, Any]] = []
        self.position_outcomes: list[dict[str, Any]] = []
        self.skipped_setups: list[dict[str, Any]] = []
        self.execution_failures: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.incomplete_timestamps: list[dict[str, Any]] = []

    def on_start(self) -> None:
        for instrument_id, bar_type in zip(
            self.config.instrument_ids,
            self.config.bar_types,
            strict=True,
        ):
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                raise RuntimeError(f"instrument not found: {instrument_id}")
            symbol = str(instrument.raw_symbol)
            self.instruments[str(instrument_id)] = instrument
            self.instrument_ids_by_symbol[symbol] = instrument_id
            self.subscribe_bars(bar_type)

    def on_bar(self, bar: Bar) -> None:
        ts_event_ns = int(bar.ts_event)
        self._advance_timestamp(ts_event_ns)
        self.seen_instruments_at_timestamp.add(str(bar.bar_type.instrument_id))
        self._manage_existing_exposure(ts_event_ns, float(bar.close.as_double()))
        if self.seen_instruments_at_timestamp == self.expected_instrument_ids:
            self._process_completed_timestamp(ts_event_ns)

    def _advance_timestamp(self, ts_event_ns: int) -> None:
        if self.current_timestamp_ns is None:
            self.current_timestamp_ns = ts_event_ns
            return
        if ts_event_ns == self.current_timestamp_ns:
            return
        if ts_event_ns < self.current_timestamp_ns:
            raise RuntimeError(
                f"multi-asset bar time moved backwards: {ts_event_ns} < {self.current_timestamp_ns}"
            )
        if self.seen_instruments_at_timestamp != self.expected_instrument_ids:
            missing = sorted(self.expected_instrument_ids - self.seen_instruments_at_timestamp)
            self.incomplete_timestamps.append(
                {"timestamp_ns": self.current_timestamp_ns, "missing_instruments": missing}
            )
            for signal in self.signals_by_time_ns.get(self.current_timestamp_ns, ()):
                self._record_skip(
                    signal,
                    "INCOMPLETE_MULTI_ASSET_TIMESTAMP",
                    self.current_timestamp_ns,
                )
            self.processed_timestamps.add(self.current_timestamp_ns)
        self.current_timestamp_ns = ts_event_ns
        self.seen_instruments_at_timestamp = set()

    def _manage_existing_exposure(self, ts_event_ns: int, reference_price: float) -> None:
        if self.current_instrument_id is None or self._global_flat():
            return
        if ts_event_ns >= self.config.trading_end_ns:
            self._request_exit("EVALUATION_WINDOW_END", ts_event_ns, reference_price)
        elif (
            self.position_open_ns is not None
            and ts_event_ns - self.position_open_ns >= self.config.maximum_hold_ns
        ):
            self._request_exit("EVENT_TIME_TIMEOUT", ts_event_ns, reference_price)

    def _process_completed_timestamp(self, ts_event_ns: int) -> None:
        if ts_event_ns in self.processed_timestamps:
            return
        self.processed_timestamps.add(ts_event_ns)
        signals = self.signals_by_time_ns.get(ts_event_ns, ())
        if not signals:
            return
        if not (self.config.trading_start_ns <= ts_event_ns < self.config.trading_end_ns):
            for signal in signals:
                self._record_skip(signal, "OUTSIDE_EVALUATION_WINDOW", ts_event_ns)
            return
        if not self._global_flat() or self._global_open_order_count() > 0 or self.entry_inflight:
            for signal in signals:
                self._record_skip(signal, "GLOBAL_PORTFOLIO_OR_ORDER_UNAVAILABLE", ts_event_ns)
            return
        if minutes_to_next_funding(ts_event_ns) <= self.config.funding_avoidance_minutes:
            for signal in signals:
                self._record_skip(signal, "FUNDING_BOUNDARY_TOO_CLOSE", ts_event_ns)
            return

        evaluations: list[tuple[float, str, AcceptanceSignal, dict[str, float]]] = []
        for signal in signals:
            geometry = self._cost_geometry(signal)
            if geometry is None:
                self._record_skip(signal, "INVALID_ROUNDED_OR_COST_AFTER_GEOMETRY", ts_event_ns)
                continue
            if geometry["net_reward_risk"] < float(self.config.minimum_net_reward_risk):
                self._record_skip(
                    signal,
                    "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET",
                    ts_event_ns,
                    geometry,
                )
                continue
            evaluations.append((geometry["net_reward_risk"], signal.symbol, signal, geometry))
        if not evaluations:
            return
        evaluations.sort(key=lambda item: (-item[0], item[1]))
        _, _, selected, geometry = evaluations[0]
        for _, _, alternate, alternate_geometry in evaluations[1:]:
            self._record_skip(
                alternate,
                "LOWER_PRIORITY_SIMULTANEOUS_SIGNAL",
                ts_event_ns,
                alternate_geometry,
            )
        self._submit_signal(selected, geometry, ts_event_ns)

    def _cost_geometry(self, signal: AcceptanceSignal) -> dict[str, float] | None:
        instrument_id = self.instrument_ids_by_symbol.get(signal.symbol)
        if instrument_id is None:
            return None
        instrument = self.instruments[str(instrument_id)]
        tick = float(instrument.price_increment.as_double())
        fee_rate = float(self.config.effective_fee_rate)
        if signal.direction > 0:
            entry = self._round_price(instrument, signal.estimated_entry, ROUND_CEILING)
            stop = self._round_price(instrument, signal.structural_stop, ROUND_FLOOR)
            target = self._round_price(instrument, signal.external_target, ROUND_FLOOR)
            valid = stop < entry < target
            gross_gain = target - entry
        else:
            entry = self._round_price(instrument, signal.estimated_entry, ROUND_FLOOR)
            stop = self._round_price(instrument, signal.structural_stop, ROUND_CEILING)
            target = self._round_price(instrument, signal.external_target, ROUND_CEILING)
            valid = target < entry < stop
            gross_gain = entry - target
        if not valid:
            return None
        expected_loss = abs(entry - stop) + fee_rate * (entry + stop) + 2.0 * tick
        expected_gain = gross_gain - fee_rate * (entry + target) - 2.0 * tick
        if expected_loss <= 0 or expected_gain <= 0:
            return None
        return {
            "estimated_entry": entry,
            "stop": stop,
            "target": target,
            "expected_loss_per_unit": expected_loss,
            "expected_gain_per_unit": expected_gain,
            "net_reward_risk": expected_gain / expected_loss,
        }

    def _submit_signal(
        self,
        signal: AcceptanceSignal,
        geometry: dict[str, float],
        ts_event_ns: int,
    ) -> None:
        instrument_id = self.instrument_ids_by_symbol[signal.symbol]
        instrument = self.instruments[str(instrument_id)]
        account = self.cache.account_for_venue(instrument_id.venue)
        if account is None:
            raise RuntimeError("shared Binance margin account was not available")
        balance = account.balance_total(self.usdt)
        if balance is None:
            raise RuntimeError("shared USDT total balance was not available")
        nav = float(balance.as_double())
        quantity_value, planned_loss = risk_sized_quantity(
            nav=nav,
            risk_fraction=float(self.config.risk_fraction),
            expected_loss_per_unit=geometry["expected_loss_per_unit"],
            size_increment=float(instrument.size_increment.as_double()),
        )
        if quantity_value <= 0:
            self._record_skip(signal, "QUANTITY_ROUNDED_TO_ZERO", ts_event_ns, {"nav": nav})
            return
        quantity = instrument.make_qty(Decimal(str(quantity_value)))
        if instrument.min_quantity is not None and quantity < instrument.min_quantity:
            self._record_skip(
                signal,
                "BELOW_VENUE_MINIMUM_QUANTITY",
                ts_event_ns,
                {"quantity": quantity_value},
            )
            return
        if (
            instrument.min_notional is not None
            and quantity.as_double() * geometry["estimated_entry"]
            < instrument.min_notional.as_double()
        ):
            self._record_skip(
                signal,
                "BELOW_VENUE_MINIMUM_NOTIONAL",
                ts_event_ns,
                {"quantity": quantity_value},
            )
            return

        order_side = OrderSide.BUY if signal.direction > 0 else OrderSide.SELL
        orders = self.order_factory.bracket(
            instrument_id=instrument_id,
            order_side=order_side,
            quantity=quantity,
            entry_order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            tp_price=instrument.make_price(Decimal(str(geometry["target"]))),
            tp_post_only=False,
            sl_trigger_price=instrument.make_price(Decimal(str(geometry["stop"]))),
            entry_tags=[
                signal.scenario_id,
                "BREAKOUT_ACCEPTANCE_CONTINUATION",
                signal.direction_name,
                "TEN_SECOND_MARKET_ENTRY",
            ],
            tp_tags=[signal.scenario_id, "COMPLETED_EXTERNAL_LIQUIDITY_TARGET"],
            sl_tags=[signal.scenario_id, "ACCEPTANCE_RETEST_INVALIDATION"],
        )
        entry_order, stop_order, target_order = orders.orders
        self.current_signal = signal
        self.current_instrument_id = instrument_id
        self.current_entry_order_id = str(entry_order.client_order_id)
        self.current_exit_order_ids = {
            str(stop_order.client_order_id),
            str(target_order.client_order_id),
        }
        self.position_open_ns = None
        self.entry_inflight = True
        self.exit_requested = False
        self.exit_request_reason = None
        self.last_fill_order_id = None
        self.trade_intents.append(
            {
                "scenario_id": signal.scenario_id,
                "scenario_family": "BREAKOUT_ACCEPTANCE_CONTINUATION",
                "symbol": signal.symbol,
                "instrument_id": str(instrument_id),
                "direction": signal.direction_name,
                "signal_time_ns": signal.signal_time_ns,
                "confirmation_time": signal.confirmation_time,
                "boundary_id": signal.boundary_id,
                "boundary_source": signal.boundary_source,
                "boundary_level": signal.boundary_level,
                "external_target_id": signal.target_id,
                "external_target_source": signal.target_source,
                "estimated_entry": geometry["estimated_entry"],
                "structural_stop": geometry["stop"],
                "external_target": geometry["target"],
                "quantity": float(quantity.as_double()),
                "nav_at_signal": nav,
                "risk_fraction": float(self.config.risk_fraction),
                "risk_budget": nav * float(self.config.risk_fraction),
                "planned_stop_loss": planned_loss,
                "expected_loss_per_unit": geometry["expected_loss_per_unit"],
                "expected_gain_per_unit": geometry["expected_gain_per_unit"],
                "net_reward_risk": geometry["net_reward_risk"],
                "entry_order_id": self.current_entry_order_id,
                "stop_order_id": str(stop_order.client_order_id),
                "target_order_id": str(target_order.client_order_id),
                "signal_details": signal.details,
            }
        )
        self._event(
            signal,
            "MARKET_OUO_SUBMITTED",
            ts_event_ns,
            {
                "quantity": float(quantity.as_double()),
                "planned_stop_loss": planned_loss,
                "net_reward_risk": geometry["net_reward_risk"],
            },
        )
        self.submit_order_list(orders)

    def on_order_filled(self, event: Any) -> None:
        order_id = str(event.client_order_id)
        self.last_fill_order_id = order_id
        if order_id == self.current_entry_order_id:
            self.entry_inflight = False

    def on_position_opened(self, event: Any) -> None:
        if self.current_signal is None:
            return
        self.entry_inflight = False
        self.position_open_ns = int(event.ts_event)
        self._event(
            self.current_signal,
            "POSITION_OPENED",
            int(event.ts_event),
            {"position_id": str(event.position_id), "avg_px_open": float(event.avg_px_open)},
        )

    def on_position_closed(self, event: Any) -> None:
        signal = self.current_signal
        if signal is None:
            return
        reason = self.exit_request_reason
        if reason is None:
            reason = (
                "BRACKET_EXIT"
                if self.last_fill_order_id in self.current_exit_order_ids
                else "UNEXPECTED_CLOSE_OR_LIQUIDATION"
            )
        self.position_outcomes.append(
            {
                "scenario_id": signal.scenario_id,
                "scenario_family": "BREAKOUT_ACCEPTANCE_CONTINUATION",
                "symbol": signal.symbol,
                "direction": signal.direction_name,
                "instrument_id": str(self.current_instrument_id),
                "position_id": str(event.position_id),
                "close_reason": reason,
                "ts_event": int(event.ts_event),
                "avg_px_close": float(event.avg_px_close),
                "last_fill_order_id": self.last_fill_order_id,
            }
        )
        self._event(
            signal,
            "POSITION_CLOSED",
            int(event.ts_event),
            {"position_id": str(event.position_id), "close_reason": reason},
        )
        self._reset_current_trade()

    def on_order_denied(self, event: Any) -> None:
        self._handle_execution_failure("ORDER_DENIED", event)

    def on_order_rejected(self, event: Any) -> None:
        self._handle_execution_failure("ORDER_REJECTED", event)

    def _handle_execution_failure(self, reason: str, event: Any) -> None:
        record = {
            "reason": reason,
            "client_order_id": str(event.client_order_id),
            "ts_event": int(event.ts_event),
            "event": str(event),
            "instrument_id": str(getattr(event, "instrument_id", "")),
        }
        self.execution_failures.append(record)
        if self.current_signal is not None:
            self._event(self.current_signal, reason, int(event.ts_event), record)
        if not self._global_flat():
            self._request_exit(reason, int(event.ts_event), 0.0)
        else:
            self._cancel_all_global_orders()
            self._reset_current_trade()

    def _request_exit(self, reason: str, ts_event_ns: int, reference_price: float) -> None:
        if self.exit_requested:
            return
        self.exit_requested = True
        self.exit_request_reason = reason
        self._cancel_all_global_orders()
        for instrument_id in self.config.instrument_ids:
            if not self.portfolio.is_flat(instrument_id):
                self.close_all_positions(instrument_id)
        if self.current_signal is not None:
            self._event(
                self.current_signal,
                "EXIT_REQUESTED",
                ts_event_ns,
                {"reason": reason, "reference_price": reference_price},
            )

    def _record_skip(
        self,
        signal: AcceptanceSignal,
        reason: str,
        ts_event_ns: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.skipped_setups.append(
            {
                "scenario_id": signal.scenario_id,
                "symbol": signal.symbol,
                "direction": signal.direction_name,
                "signal_time_ns": signal.signal_time_ns,
                "observed_time_ns": ts_event_ns,
                "boundary_source": signal.boundary_source,
                "target_source": signal.target_source,
                "reason": reason,
                **dict(details or {}),
            }
        )

    def _event(
        self,
        signal: AcceptanceSignal,
        event_type: str,
        ts_event_ns: int,
        details: dict[str, Any],
    ) -> None:
        self.events.append(
            {
                "scenario_id": signal.scenario_id,
                "symbol": signal.symbol,
                "instrument_id": str(self.instrument_ids_by_symbol.get(signal.symbol, "")),
                "event_type": event_type,
                "event_time_ns": ts_event_ns,
                "observed_time_ns": ts_event_ns,
                "details": dict(details),
            }
        )

    def _global_flat(self) -> bool:
        return all(self.portfolio.is_flat(item) for item in self.config.instrument_ids)

    def _global_open_order_count(self) -> int:
        return sum(
            len(self.cache.orders_open(instrument_id=item))
            for item in self.config.instrument_ids
        )

    def _cancel_all_global_orders(self) -> None:
        for instrument_id in self.config.instrument_ids:
            self.cancel_all_orders(instrument_id)

    def _reset_current_trade(self) -> None:
        self.current_signal = None
        self.current_instrument_id = None
        self.current_entry_order_id = None
        self.current_exit_order_ids.clear()
        self.position_open_ns = None
        self.entry_inflight = False
        self.exit_requested = False
        self.exit_request_reason = None
        self.last_fill_order_id = None

    def on_stop(self) -> None:
        self._cancel_all_global_orders()
        for instrument_id in self.config.instrument_ids:
            if not self.portfolio.is_flat(instrument_id):
                self.close_all_positions(instrument_id)

    @staticmethod
    def _round_price(instrument: Any, value: float, rounding: str) -> float:
        tick = instrument.price_increment.as_decimal()
        units = (Decimal(str(value)) / tick).to_integral_value(rounding=rounding)
        return float(units * tick)
