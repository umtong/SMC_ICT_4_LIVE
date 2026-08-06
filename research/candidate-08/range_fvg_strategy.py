"""NautilusTrader execution adapter for completed-range FVG scenarios."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from logic import minutes_to_next_funding, risk_sized_quantity
from range_fvg_logic import Direction, LogicEvent, RangeFVGSignal


class RangeFVGStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trading_start_ns: int
    trading_end_ns: int
    risk_fraction: Decimal
    effective_fee_rate: Decimal
    minimum_net_reward_risk: Decimal
    entry_expiry_bars: int
    maximum_hold_bars: int
    funding_avoidance_minutes: int


class RangeFVGStrategy(Strategy):
    """Execute one causal limit-entry range/FVG scenario at a time."""

    def __init__(
        self,
        config: RangeFVGStrategyConfig,
        signals_by_time_ns: dict[int, tuple[RangeFVGSignal, ...]],
    ):
        super().__init__(config=config)
        self.signals_by_time_ns = signals_by_time_ns
        self.instrument = None
        self.usdt = Currency.from_str("USDT")
        self.bar_index = -1
        self.position_open_index: int | None = None
        self.current_signal: RangeFVGSignal | None = None
        self.current_scenario_id: str | None = None
        self.current_family: str | None = None
        self.current_direction: str | None = None
        self.current_entry_order = None
        self.current_entry_order_id: str | None = None
        self.current_exit_order_ids: set[str] = set()
        self.current_scenario_state: str | None = None
        self.entry_submitted_index: int | None = None
        self.entry_inflight = False
        self.entry_cancel_requested = False
        self.exit_requested = False
        self.exit_request_reason: str | None = None
        self.last_fill_order_id: str | None = None
        self.events: list[LogicEvent] = []
        self.trade_intents: list[dict[str, Any]] = []
        self.position_outcomes: list[dict[str, Any]] = []
        self.execution_failures: list[dict[str, Any]] = []
        self.skipped_setups: list[dict[str, Any]] = []

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            raise RuntimeError(f"instrument not found: {self.config.instrument_id}")
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        self.bar_index += 1
        ts_event_ns = int(bar.ts_event)
        high = float(bar.high.as_double())
        low = float(bar.low.as_double())
        close = float(bar.close.as_double())
        within_window = self.config.trading_start_ns <= ts_event_ns < self.config.trading_end_ns
        flat = self.portfolio.is_flat(self.config.instrument_id)

        if not flat:
            if ts_event_ns >= self.config.trading_end_ns:
                self._request_exit("EVALUATION_WINDOW_END", ts_event_ns, close)
            elif (
                self.position_open_index is not None
                and self.bar_index - self.position_open_index >= self.config.maximum_hold_bars
            ):
                self._request_exit("EVENT_TIME_TIMEOUT", ts_event_ns, close)
            return

        if self.entry_inflight:
            self._manage_unfilled_entry(
                ts_event_ns=ts_event_ns,
                high=high,
                low=low,
                close=close,
                within_window=within_window,
            )
            return
        if self.entry_cancel_requested or self.exit_requested:
            return

        signals = self.signals_by_time_ns.get(ts_event_ns, ())
        if not signals:
            return
        for signal in signals:
            self.events.extend(signal.events)

        no_orders = len(self.cache.orders_open(instrument_id=self.config.instrument_id)) == 0
        funding_safe = minutes_to_next_funding(ts_event_ns) > self.config.funding_avoidance_minutes
        if not within_window or not no_orders or not funding_safe:
            reason = (
                "OUTSIDE_EVALUATION_WINDOW"
                if not within_window
                else "PORTFOLIO_OR_ORDER_NOT_AVAILABLE"
                if not no_orders
                else "FUNDING_BOUNDARY_TOO_CLOSE"
            )
            for signal in signals:
                self._record_skip(signal, reason, ts_event_ns, close, {})
            return

        evaluations: list[tuple[float, RangeFVGSignal, dict[str, float]]] = []
        for signal in signals:
            geometry = self._cost_geometry(signal)
            if geometry is None:
                self._record_skip(
                    signal,
                    "INVALID_ROUNDED_OR_COST_AFTER_GEOMETRY",
                    ts_event_ns,
                    close,
                    {},
                )
                continue
            net_rr = geometry["net_reward_risk"]
            if net_rr < float(self.config.minimum_net_reward_risk):
                self._record_skip(
                    signal,
                    "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET",
                    ts_event_ns,
                    close,
                    geometry,
                )
                continue
            evaluations.append((net_rr, signal, geometry))

        if not evaluations:
            return
        evaluations.sort(
            key=lambda item: (
                item[0],
                item[1].boundary_source.value,
                item[1].external_target_source.value,
            ),
            reverse=True,
        )
        _, selected, geometry = evaluations[0]
        for _, alternate, alternate_geometry in evaluations[1:]:
            self._record_skip(
                alternate,
                "LOWER_PRIORITY_SIMULTANEOUS_SCENARIO",
                ts_event_ns,
                close,
                alternate_geometry,
            )
        self._submit_signal(selected, geometry, ts_event_ns)

    def _cost_geometry(self, signal: RangeFVGSignal) -> dict[str, float] | None:
        assert self.instrument is not None
        tick = float(self.instrument.price_increment.as_double())
        fee_rate = float(self.config.effective_fee_rate)
        if signal.direction is Direction.LONG:
            entry = self._round_price(signal.limit_entry, ROUND_FLOOR)
            stop = self._round_price(signal.structural_stop, ROUND_FLOOR)
            target = self._round_price(signal.external_target, ROUND_FLOOR)
            valid = stop < entry < target
            gross_gain = target - entry
        else:
            entry = self._round_price(signal.limit_entry, ROUND_CEILING)
            stop = self._round_price(signal.structural_stop, ROUND_CEILING)
            target = self._round_price(signal.external_target, ROUND_CEILING)
            valid = target < entry < stop
            gross_gain = entry - target
        if not valid:
            return None
        loss = abs(entry - stop) + fee_rate * (entry + stop) + 2.0 * tick
        gain = gross_gain - fee_rate * (entry + target) - 2.0 * tick
        if loss <= 0 or gain <= 0:
            return None
        return {
            "entry": entry,
            "stop": stop,
            "target": target,
            "expected_loss_per_unit": loss,
            "expected_gain_per_unit": gain,
            "net_reward_risk": gain / loss,
        }

    def _submit_signal(
        self,
        signal: RangeFVGSignal,
        geometry: dict[str, float],
        ts_event_ns: int,
    ) -> None:
        assert self.instrument is not None
        account = self.cache.account_for_venue(self.config.instrument_id.venue)
        if account is None:
            raise RuntimeError("margin account was not available for NAV sizing")
        balance = account.balance_total(self.usdt)
        if balance is None:
            raise RuntimeError("USDT total balance was not available for NAV sizing")
        nav = float(balance.as_double())
        size_increment = float(self.instrument.size_increment.as_double())
        quantity_value, planned_loss = risk_sized_quantity(
            nav=nav,
            risk_fraction=float(self.config.risk_fraction),
            expected_loss_per_unit=geometry["expected_loss_per_unit"],
            size_increment=size_increment,
        )
        if quantity_value <= 0:
            self._record_skip(signal, "QUANTITY_ROUNDED_TO_ZERO", ts_event_ns, geometry["entry"], {"nav": nav})
            return
        quantity = self.instrument.make_qty(Decimal(str(quantity_value)))
        min_qty = self.instrument.min_quantity
        if min_qty is not None and quantity < min_qty:
            self._record_skip(
                signal,
                "BELOW_VENUE_MINIMUM_QUANTITY",
                ts_event_ns,
                geometry["entry"],
                {"quantity": quantity_value},
            )
            return
        min_notional = self.instrument.min_notional
        if min_notional is not None and quantity.as_double() * geometry["entry"] < min_notional.as_double():
            self._record_skip(
                signal,
                "BELOW_VENUE_MINIMUM_NOTIONAL",
                ts_event_ns,
                geometry["entry"],
                {"quantity": quantity_value},
            )
            return

        order_side = OrderSide.BUY if signal.direction is Direction.LONG else OrderSide.SELL
        entry_price = self.instrument.make_price(Decimal(str(geometry["entry"])))
        stop_price = self.instrument.make_price(Decimal(str(geometry["stop"])))
        target_price = self.instrument.make_price(Decimal(str(geometry["target"])))
        orders = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=quantity,
            entry_order_type=OrderType.LIMIT,
            entry_price=entry_price,
            entry_post_only=False,
            time_in_force=TimeInForce.GTC,
            tp_price=target_price,
            tp_post_only=False,
            sl_trigger_price=stop_price,
            entry_tags=[signal.scenario_id, signal.family.value, signal.direction.value, "FVG_LIMIT_ENTRY"],
            tp_tags=[signal.scenario_id, signal.family.value, "EXTERNAL_LIQUIDITY_TARGET"],
            sl_tags=[signal.scenario_id, signal.family.value, "STRUCTURAL_INVALIDATION"],
        )
        entry_order, stop_order, target_order = orders.orders
        self.current_signal = signal
        self.current_scenario_id = signal.scenario_id
        self.current_family = signal.family.value
        self.current_direction = signal.direction.value
        self.current_entry_order = entry_order
        self.current_entry_order_id = str(entry_order.client_order_id)
        self.current_exit_order_ids = {
            str(stop_order.client_order_id),
            str(target_order.client_order_id),
        }
        self.current_scenario_state = "CONFIRMED"
        self.entry_submitted_index = self.bar_index
        self.entry_inflight = True
        self.entry_cancel_requested = False
        self.trade_intents.append(
            {
                "scenario_id": signal.scenario_id,
                "scenario_family": signal.family.value,
                "direction": signal.direction.value,
                "signal_index": signal.signal_index,
                "signal_time_ns": signal.signal_time_ns,
                "boundary_id": signal.boundary_id,
                "boundary_source": signal.boundary_source.value,
                "boundary_level": signal.boundary_level,
                "fvg_low": signal.fvg_low,
                "fvg_high": signal.fvg_high,
                "estimated_entry": geometry["entry"],
                "structural_stop": geometry["stop"],
                "external_target_id": signal.external_target_id,
                "external_target_source": signal.external_target_source.value,
                "external_target": geometry["target"],
                "quantity": float(quantity.as_double()),
                "nav_at_signal": nav,
                "risk_fraction": float(self.config.risk_fraction),
                "risk_budget": nav * float(self.config.risk_fraction),
                "planned_stop_loss": planned_loss,
                "expected_loss_per_unit": geometry["expected_loss_per_unit"],
                "expected_gain_per_unit": geometry["expected_gain_per_unit"],
                "net_reward_risk": geometry["net_reward_risk"],
                "entry_order_id": str(entry_order.client_order_id),
                "stop_order_id": str(stop_order.client_order_id),
                "target_order_id": str(target_order.client_order_id),
                "logic_details": signal.details,
            }
        )
        self._transition(
            signal.scenario_id,
            event_type="FVG_LIMIT_BRACKET_SUBMITTED",
            observed_time_ns=ts_event_ns,
            next_state="ORDER_SUBMITTED",
            reason_code="NAV_RISK_SIZED_FVG_LIMIT_OUO",
            reference_price=geometry["entry"],
            details={
                "quantity": float(quantity.as_double()),
                "planned_loss": planned_loss,
                "net_reward_risk": geometry["net_reward_risk"],
                "entry_expiry_bars": int(self.config.entry_expiry_bars),
            },
        )
        self.submit_order_list(orders)

    def _manage_unfilled_entry(
        self,
        *,
        ts_event_ns: int,
        high: float,
        low: float,
        close: float,
        within_window: bool,
    ) -> None:
        signal = self.current_signal
        if signal is None or self.entry_cancel_requested:
            return
        expired = (
            self.entry_submitted_index is not None
            and self.bar_index - self.entry_submitted_index >= self.config.entry_expiry_bars
        )
        if signal.direction is Direction.LONG:
            invalidated = low <= signal.invalidation_before_fill
            target_passed = high >= signal.external_target
        else:
            invalidated = high >= signal.invalidation_before_fill
            target_passed = low <= signal.external_target
        if not within_window:
            reason = "EVALUATION_WINDOW_END_BEFORE_ENTRY"
        elif invalidated:
            reason = "STRUCTURE_INVALIDATED_BEFORE_LIMIT_FILL"
        elif target_passed:
            reason = "EXTERNAL_TARGET_REACHED_BEFORE_LIMIT_FILL"
        elif expired:
            reason = "FVG_ENTRY_EXPIRED"
        else:
            return
        if self.current_entry_order is not None:
            self.cancel_order(self.current_entry_order)
            self.entry_cancel_requested = True
            self._transition(
                signal.scenario_id,
                event_type="ENTRY_CANCEL_REQUESTED",
                observed_time_ns=ts_event_ns,
                next_state="ENTRY_CANCEL_REQUESTED",
                reason_code=reason,
                reference_price=close,
            )

    def _request_exit(self, reason: str, ts_event_ns: int, close: float) -> None:
        if self.exit_requested:
            return
        self.exit_requested = True
        self.exit_request_reason = reason
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                event_type="EXIT_REQUESTED",
                observed_time_ns=ts_event_ns,
                next_state="EXIT_REQUESTED",
                reason_code=reason,
                reference_price=close,
            )

    def on_order_filled(self, event: Any) -> None:
        order_id = str(event.client_order_id)
        self.last_fill_order_id = order_id
        if order_id == self.current_entry_order_id:
            self.entry_inflight = False
            self.entry_cancel_requested = False

    def on_order_canceled(self, event: Any) -> None:
        if str(event.client_order_id) != self.current_entry_order_id:
            return
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                event_type="ENTRY_CANCELED",
                observed_time_ns=int(event.ts_event),
                next_state="CANCELED",
                reason_code="UNFILLED_FVG_LIMIT_CANCELED",
            )
        self._reset_flat_state()

    def on_order_expired(self, event: Any) -> None:
        if str(event.client_order_id) != self.current_entry_order_id:
            return
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                event_type="ENTRY_EXPIRED",
                observed_time_ns=int(event.ts_event),
                next_state="CANCELED",
                reason_code="UNFILLED_FVG_LIMIT_EXPIRED",
            )
        self._reset_flat_state()

    def on_position_opened(self, event: Any) -> None:
        self.position_open_index = self.bar_index
        self.entry_inflight = False
        self.entry_cancel_requested = False
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                event_type="POSITION_OPENED",
                observed_time_ns=int(event.ts_event),
                next_state="POSITION_OPEN",
                reason_code="FVG_LIMIT_FILLED",
                reference_price=float(event.avg_px_open),
                details={"position_id": str(event.position_id)},
            )

    def on_position_closed(self, event: Any) -> None:
        reason = self.exit_request_reason
        if reason is None:
            reason = (
                "BRACKET_EXIT"
                if self.last_fill_order_id in self.current_exit_order_ids
                else "UNEXPECTED_CLOSE_OR_LIQUIDATION"
            )
        scenario_id = self.current_scenario_id or "unknown-position"
        self.position_outcomes.append(
            {
                "scenario_id": scenario_id,
                "scenario_family": self.current_family,
                "direction": self.current_direction,
                "position_id": str(event.position_id),
                "close_reason": reason,
                "ts_event": int(event.ts_event),
                "last_fill_order_id": self.last_fill_order_id,
            }
        )
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                event_type="POSITION_CLOSED",
                observed_time_ns=int(event.ts_event),
                next_state="CLOSED",
                reason_code=reason,
                reference_price=float(event.avg_px_close),
                details={"position_id": str(event.position_id)},
            )
        self._reset_flat_state()

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
        }
        self.execution_failures.append(record)
        if self.current_scenario_id is None:
            return
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
            self.exit_requested = True
            self.exit_request_reason = reason
            next_state = "EXIT_REQUESTED"
        else:
            next_state = "FAILED"
        self._transition(
            self.current_scenario_id,
            event_type=reason,
            observed_time_ns=int(event.ts_event),
            next_state=next_state,
            reason_code=reason,
            details=record,
        )
        if next_state == "FAILED":
            self._reset_flat_state()

    def _record_skip(
        self,
        signal: RangeFVGSignal,
        reason: str,
        ts_event_ns: int,
        reference_price: float,
        details: dict[str, Any],
    ) -> None:
        self.skipped_setups.append(
            {
                "scenario_id": signal.scenario_id,
                "scenario_family": signal.family.value,
                "direction": signal.direction.value,
                "signal_time_ns": signal.signal_time_ns,
                "boundary_source": signal.boundary_source.value,
                "target_source": signal.external_target_source.value,
                "reason": reason,
                **details,
            }
        )
        previous_state = signal.events[-1].next_state if signal.events else "CONFIRMED"
        self.events.append(
            LogicEvent(
                scenario_id=signal.scenario_id,
                event_type="SETUP_SKIPPED",
                event_time_ns=ts_event_ns,
                observed_time_ns=ts_event_ns,
                previous_state=previous_state,
                next_state="SKIPPED",
                reason_code=reason,
                reference_price=reference_price,
                details=dict(details),
            )
        )

    def _transition(
        self,
        scenario_id: str,
        *,
        event_type: str,
        observed_time_ns: int,
        next_state: str,
        reason_code: str,
        reference_price: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        previous_state = self.current_scenario_state or "CONFIRMED"
        self.events.append(
            LogicEvent(
                scenario_id=scenario_id,
                event_type=event_type,
                event_time_ns=observed_time_ns,
                observed_time_ns=observed_time_ns,
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=reference_price,
                details=dict(details or {}),
            )
        )
        self.current_scenario_state = next_state

    def _reset_flat_state(self) -> None:
        self.position_open_index = None
        self.current_signal = None
        self.current_scenario_id = None
        self.current_family = None
        self.current_direction = None
        self.current_entry_order = None
        self.current_entry_order_id = None
        self.current_exit_order_ids.clear()
        self.current_scenario_state = None
        self.entry_submitted_index = None
        self.entry_inflight = False
        self.entry_cancel_requested = False
        self.exit_requested = False
        self.exit_request_reason = None
        self.last_fill_order_id = None

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.close_all_positions(self.config.instrument_id)

    def _round_price(self, value: float, rounding: str) -> float:
        assert self.instrument is not None
        tick = self.instrument.price_increment.as_decimal()
        units = (Decimal(str(value)) / tick).to_integral_value(rounding=rounding)
        return float(units * tick)
