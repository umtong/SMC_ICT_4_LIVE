"""NautilusTrader execution strategy for causal aggregate-trade signals."""
from __future__ import annotations

from decimal import Decimal
import json
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType, CustomData, DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderRejected, PositionClosed, PositionOpened
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from event_signal_data import CausalTradeSignal, EVENT_SIGNAL_CLIENT_ID
from smc_ict_4.contracts import ResearchEvent


NS_PER_SECOND = 1_000_000_000


class Candidate07EventSignalStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_start_ns: int
    trade_end_ns: int
    initial_nav: Decimal
    risk_fraction: Decimal
    risk_funding_reserve_bps: Decimal
    maximum_hold_seconds: int
    minimum_rr: Decimal


class Candidate07EventSignalStrategy(Strategy):
    """Submit one cost-aware bracket for each eligible causal signal."""

    def __init__(self, config: Candidate07EventSignalStrategyConfig):
        super().__init__(config)
        self._instrument = None
        self._last_bar: Bar | None = None
        self._active_signal: CausalTradeSignal | None = None
        self._active_entry_nav: float | None = None
        self._position_open_ns: int | None = None
        self._exit_pending = False
        self._events: list[ResearchEvent] = []
        self._nav_series: list[dict[str, Any]] = []
        self._trades: list[dict[str, Any]] = []
        self._diagnostics: list[dict[str, Any]] = []
        self._last_nav = float(config.initial_nav)
        self._quote_currency = Currency.from_str("USDT")

    @property
    def research_events(self) -> tuple[ResearchEvent, ...]:
        return tuple(self._events)

    @property
    def nav_series(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._nav_series)

    @property
    def trade_diagnostics(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._trades)

    @property
    def scenario_diagnostics(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._diagnostics)

    def on_start(self) -> None:
        self._instrument = self.cache.instrument(self.config.instrument_id)
        if self._instrument is None:
            raise RuntimeError(
                f"instrument missing from cache: {self.config.instrument_id}"
            )
        self.subscribe_bars(self.config.bar_type)
        self.subscribe_data(
            DataType(CausalTradeSignal),
            client_id=EVENT_SIGNAL_CLIENT_ID,
            instrument_id=self.config.instrument_id,
        )

    def on_data(self, data: Any) -> None:
        payload = data.data if isinstance(data, CustomData) else data
        if not isinstance(payload, CausalTradeSignal):
            return
        if payload.instrument_id != self.config.instrument_id:
            return
        now = int(payload.ts_event)
        self._record_nav(now)
        in_window = (
            self.config.trade_start_ns
            <= payload.observed_time_ns
            < self.config.trade_end_ns
        )
        flat = self.portfolio.is_flat(self.config.instrument_id)
        if (
            not in_window
            or not flat
            or self._active_signal is not None
            or self._exit_pending
        ):
            self._diagnostics.append(
                {
                    "scenario_id": payload.scenario_id,
                    "reason": "SIGNAL_SLOT_OR_WINDOW_INELIGIBLE",
                    "ts_event_ns": now,
                    "in_window": in_window,
                    "portfolio_flat": flat,
                    "active_signal": (
                        None
                        if self._active_signal is None
                        else self._active_signal.scenario_id
                    ),
                }
            )
            return
        if self._last_bar is None:
            self._diagnostics.append(
                {
                    "scenario_id": payload.scenario_id,
                    "reason": "NO_COMPLETED_EXECUTION_BAR",
                    "ts_event_ns": now,
                }
            )
            return
        self._submit_signal(payload, self._last_bar)

    def on_bar(self, bar: Bar) -> None:
        now = int(bar.ts_event)
        self._last_bar = bar
        self._record_nav(now)
        flat = self.portfolio.is_flat(self.config.instrument_id)

        if not flat and self._position_open_ns is not None:
            held_ns = now - self._position_open_ns
            if (
                held_ns >= self.config.maximum_hold_seconds * NS_PER_SECOND
                and not self._exit_pending
            ):
                self._exit_pending = True
                self.cancel_all_orders(self.config.instrument_id)
                self.close_all_positions(self.config.instrument_id)

        if now >= self.config.trade_end_ns - NS_PER_SECOND:
            if not self._exit_pending:
                self._exit_pending = True
                self.cancel_all_orders(self.config.instrument_id)
                if not flat:
                    self.close_all_positions(self.config.instrument_id)

    def on_position_opened(self, event: PositionOpened) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        self._position_open_ns = int(event.ts_event)
        self._exit_pending = False
        signal = self._active_signal
        if signal is not None:
            self._append_event(
                scenario_id=signal.scenario_id,
                previous_state="ORDER_SUBMITTED",
                next_state="POSITION_OPEN",
                reason_code="NAUTILUS_POSITION_OPENED",
                event_time_ns=int(event.ts_event),
                reference_price=signal.entry_reference,
                details={"position_id": str(event.position_id)},
            )

    def on_position_closed(self, event: PositionClosed) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        nav_after = self._current_nav()
        signal = self._active_signal
        nav_before = (
            self._active_entry_nav
            if self._active_entry_nav is not None
            else nav_after
        )
        net_pnl = nav_after - nav_before
        if signal is not None:
            details = json.loads(signal.details_json)
            self._trades.append(
                {
                    "scenario_id": signal.scenario_id,
                    "kind": signal.signal_kind,
                    "direction": signal.direction,
                    "entry_reference": signal.entry_reference,
                    "stop_price": signal.stop_price,
                    "target_price": signal.target_price,
                    "expected_rr": signal.expected_rr,
                    "source_pool_id": signal.source_pool_id,
                    "opened_ns": self._position_open_ns,
                    "closed_ns": int(event.ts_event),
                    "nav_before": nav_before,
                    "nav_after": nav_after,
                    "net_pnl": net_pnl,
                    "net_return_on_nav": (
                        nav_after / nav_before - 1.0
                        if nav_before > 0.0
                        else 0.0
                    ),
                    "position_id": str(event.position_id),
                    "signal_details": details,
                }
            )
            self._append_event(
                scenario_id=signal.scenario_id,
                previous_state="POSITION_OPEN",
                next_state="TERMINAL",
                reason_code="NAUTILUS_POSITION_CLOSED",
                event_time_ns=int(event.ts_event),
                reference_price=(
                    signal.target_price if net_pnl > 0.0 else signal.stop_price
                ),
                details={"net_pnl": net_pnl, "nav_after": nav_after},
            )
        self._active_signal = None
        self._active_entry_nav = None
        self._position_open_ns = None
        self._exit_pending = False
        self._record_nav(int(event.ts_event))

    def on_order_rejected(self, event: OrderRejected) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        signal = self._active_signal
        if signal is None:
            return
        self._append_event(
            scenario_id=signal.scenario_id,
            previous_state="ORDER_SUBMITTED",
            next_state="INVALIDATED",
            reason_code="NAUTILUS_ORDER_REJECTED",
            event_time_ns=int(event.ts_event),
            reference_price=signal.entry_reference,
            details={"reason": str(event.reason)},
        )
        if self.portfolio.is_flat(self.config.instrument_id):
            self._active_signal = None
            self._active_entry_nav = None

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.close_all_positions(self.config.instrument_id)

    def _submit_signal(self, signal: CausalTradeSignal, bar: Bar) -> None:
        if self._instrument is None:
            raise RuntimeError("instrument is not initialized")
        current_price = Decimal(str(bar.close.as_double()))
        stop = Decimal(str(signal.stop_price))
        target = Decimal(str(signal.target_price))
        if signal.direction == "LONG":
            risk_distance = current_price - stop
            reward_distance = target - current_price
            side = OrderSide.BUY
        else:
            risk_distance = stop - current_price
            reward_distance = current_price - target
            side = OrderSide.SELL
        if risk_distance <= 0 or reward_distance <= 0:
            self._diagnostics.append(
                {
                    "scenario_id": signal.scenario_id,
                    "reason": "SIGNAL_GEOMETRY_ALREADY_INVALID",
                    "current_price": float(current_price),
                    "stop": float(stop),
                    "target": float(target),
                }
            )
            return
        actual_rr = reward_distance / risk_distance
        if actual_rr < self.config.minimum_rr:
            self._diagnostics.append(
                {
                    "scenario_id": signal.scenario_id,
                    "reason": "SIGNAL_RR_ERODED_BEFORE_SUBMISSION",
                    "actual_rr": float(actual_rr),
                    "minimum_rr": float(self.config.minimum_rr),
                }
            )
            return

        equity = Decimal(str(self._current_nav()))
        planned_budget = equity * self.config.risk_fraction
        tick = self._instrument.price_increment.as_decimal()
        expected_entry_fill = (
            current_price + tick
            if signal.direction == "LONG"
            else current_price - tick
        )
        expected_stop_fill = (
            stop - tick if signal.direction == "LONG" else stop + tick
        )
        fee_rate = self._instrument.taker_fee or Decimal(0)
        funding_reserve = (
            expected_entry_fill
            * self.config.risk_funding_reserve_bps
            / Decimal(10_000)
        )
        per_unit_loss = (
            abs(expected_entry_fill - expected_stop_fill)
            + expected_entry_fill * fee_rate
            + expected_stop_fill * fee_rate
            + funding_reserve
        )
        if per_unit_loss <= 0:
            self._diagnostics.append(
                {
                    "scenario_id": signal.scenario_id,
                    "reason": "NONPOSITIVE_UNIT_LOSS",
                }
            )
            return
        quantity = self._instrument.make_qty(planned_budget / per_unit_loss)
        if quantity.as_decimal() <= 0:
            self._diagnostics.append(
                {
                    "scenario_id": signal.scenario_id,
                    "reason": "QUANTITY_ROUNDED_TO_ZERO",
                }
            )
            return

        stop_price = self._instrument.make_price(stop)
        target_price = self._instrument.make_price(target)
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            sl_trigger_price=stop_price,
            tp_price=target_price,
        )
        self._active_signal = CausalTradeSignal(
            instrument_id=signal.instrument_id,
            scenario_id=signal.scenario_id,
            direction=signal.direction,
            entry_reference=float(current_price),
            stop_price=stop_price.as_double(),
            target_price=target_price.as_double(),
            expected_rr=float(actual_rr),
            source_pool_id=signal.source_pool_id,
            signal_kind=signal.signal_kind,
            details_json=json.dumps(
                {
                    **json.loads(signal.details_json),
                    "planned_loss_budget": float(planned_budget),
                    "per_unit_expected_loss": float(per_unit_loss),
                    "quantity": str(quantity),
                    "fee_rate": str(fee_rate),
                    "funding_reserve_bps": str(
                        self.config.risk_funding_reserve_bps
                    ),
                    "slippage_ticks_each_adverse_fill": 1,
                    "signal_delivery_ns": signal.ts_event,
                },
                sort_keys=True,
            ),
            observed_time_ns=signal.observed_time_ns,
            ts_event=signal.ts_event,
            ts_init=signal.ts_init,
        )
        self._active_entry_nav = float(equity)
        # Nautilus can synchronously match the market parent while
        # submit_order_list is still on the stack.  Record ORDER_SUBMITTED first
        # so PositionOpened and OrderRejected callbacks extend, rather than
        # overtake, the append-only state chain.
        self._append_event(
            scenario_id=signal.scenario_id,
            previous_state="ENTRY_READY",
            next_state="ORDER_SUBMITTED",
            reason_code="NAUTILUS_BRACKET_SUBMITTED",
            event_time_ns=int(signal.ts_event),
            reference_price=float(current_price),
            details={
                "quantity": str(quantity),
                "planned_loss_budget": float(planned_budget),
                "actual_rr": float(actual_rr),
            },
        )
        self.submit_order_list(order_list)

    def _current_nav(self) -> float:
        try:
            equities = self.portfolio.equity(self.config.instrument_id.venue)
            money = equities.get(self._quote_currency)
            if money is not None:
                value = money.as_double()
                if value > 0.0:
                    self._last_nav = value
                    return value
        except Exception:
            pass
        return self._last_nav

    def _record_nav(self, timestamp_ns: int) -> None:
        nav = self._current_nav()
        if (
            self._nav_series
            and self._nav_series[-1]["timestamp_ns"] == timestamp_ns
        ):
            self._nav_series[-1]["nav"] = nav
        else:
            self._nav_series.append(
                {"timestamp_ns": timestamp_ns, "nav": nav}
            )

    def _append_event(
        self,
        *,
        scenario_id: str,
        previous_state: str,
        next_state: str,
        reason_code: str,
        event_time_ns: int,
        reference_price: float,
        details: dict[str, Any],
    ) -> None:
        self._events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=str(self.config.instrument_id),
                event_type="EXECUTION_TRANSITION",
                event_time_ns=event_time_ns,
                observed_time_ns=event_time_ns,
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=str(reference_price),
                details=details,
            )
        )


__all__ = [
    "Candidate07EventSignalStrategy",
    "Candidate07EventSignalStrategyConfig",
]
