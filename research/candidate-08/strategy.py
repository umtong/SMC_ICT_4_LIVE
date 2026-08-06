"""NautilusTrader strategy adapter for candidate-08 liquidity bifurcation logic."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from logic import (
    BarPoint,
    Direction,
    LiquidityBifurcationLogic,
    LogicConfig,
    LogicEvent,
    TradeSetup,
    minutes_to_next_funding,
    net_reward_risk,
    risk_sized_quantity,
)


class Candidate08StrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trading_start_ns: int
    trading_end_ns: int
    risk_fraction: Decimal
    effective_fee_rate: Decimal
    minimum_net_reward_risk: Decimal
    maximum_hold_bars: int
    funding_avoidance_minutes: int


class Candidate08Strategy(Strategy):
    """Translate causal scenario confirmations into risk-sized bracket orders."""

    def __init__(self, config: Candidate08StrategyConfig, logic_config: LogicConfig):
        super().__init__(config=config)
        self.logic = LiquidityBifurcationLogic(logic_config)
        self.instrument = None
        self.usdt = Currency.from_str("USDT")
        self.bar_index = -1
        self.position_open_index: int | None = None
        self.current_scenario_id: str | None = None
        self.current_family: str | None = None
        self.current_direction: str | None = None
        self.current_entry_order_id: str | None = None
        self.current_exit_order_ids: set[str] = set()
        self.current_scenario_state: str | None = None
        self.entry_inflight = False
        self.exit_requested = False
        self.exit_request_reason: str | None = None
        self.last_fill_order_id: str | None = None
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
        point = BarPoint(
            index=self.bar_index,
            ts_event_ns=int(bar.ts_event),
            open=float(bar.open.as_double()),
            high=float(bar.high.as_double()),
            low=float(bar.low.as_double()),
            close=float(bar.close.as_double()),
            volume=float(bar.volume.as_double()),
        )

        within_window = self.config.trading_start_ns <= point.ts_event_ns < self.config.trading_end_ns
        flat = self.portfolio.is_flat(self.config.instrument_id)

        if not flat:
            if point.ts_event_ns >= self.config.trading_end_ns:
                self._request_exit("EVALUATION_WINDOW_END", point)
            elif (
                self.position_open_index is not None
                and self.bar_index - self.position_open_index >= self.config.maximum_hold_bars
            ):
                self._request_exit("EVENT_TIME_TIMEOUT", point)
            self.logic.on_bar(point, trading_available=False)
            return

        if self.exit_requested:
            # The close fill has not reached the strategy yet.  Do not arm another trade.
            self.logic.on_bar(point, trading_available=False)
            return

        no_orders = len(self.cache.orders_open(instrument_id=self.config.instrument_id)) == 0
        funding_safe = (
            minutes_to_next_funding(point.ts_event_ns) > self.config.funding_avoidance_minutes
        )
        trading_available = (
            within_window
            and no_orders
            and not self.entry_inflight
            and funding_safe
        )
        setups = self.logic.on_bar(point, trading_available=trading_available)
        if not setups:
            return
        if len(setups) != 1:
            raise RuntimeError("candidate-08 logic must emit at most one setup per bar")
        self._submit_setup(setups[0], point)

    def _submit_setup(self, setup: TradeSetup, bar: BarPoint) -> None:
        assert self.instrument is not None
        tick = float(self.instrument.price_increment.as_double())
        size_increment = float(self.instrument.size_increment.as_double())
        fee_rate = float(self.config.effective_fee_rate)

        if setup.direction is Direction.LONG:
            entry_estimate = bar.close + tick
            stop_value = self._round_price(setup.structural_stop, ROUND_FLOOR)
            target_value = self._round_price(setup.liquidity_target, ROUND_FLOOR)
            order_side = OrderSide.BUY
            geometry_valid = stop_value < entry_estimate < target_value
        else:
            entry_estimate = bar.close - tick
            stop_value = self._round_price(setup.structural_stop, ROUND_CEILING)
            target_value = self._round_price(setup.liquidity_target, ROUND_CEILING)
            order_side = OrderSide.SELL
            geometry_valid = target_value < entry_estimate < stop_value

        if not geometry_valid:
            self._skip_setup(setup, "INVALID_ROUNDED_GEOMETRY", bar, {})
            self.logic.mark_trade(setup.signal_index)
            return

        expected_loss, expected_gain, net_rr = net_reward_risk(
            direction=setup.direction,
            entry=entry_estimate,
            stop=stop_value,
            target=target_value,
            fee_rate=fee_rate,
            tick_size=tick,
        )
        if expected_gain <= 0 or net_rr < float(self.config.minimum_net_reward_risk):
            self._skip_setup(
                setup,
                "INSUFFICIENT_COST_AFTER_REWARD_RISK",
                bar,
                {"expected_loss_per_unit": expected_loss, "expected_gain_per_unit": expected_gain, "net_rr": net_rr},
            )
            self.logic.mark_trade(setup.signal_index)
            return

        account = self.cache.account_for_venue(self.config.instrument_id.venue)
        if account is None:
            raise RuntimeError("margin account was not available for NAV sizing")
        balance = account.balance_total(self.usdt)
        if balance is None:
            raise RuntimeError("USDT total balance was not available for NAV sizing")
        nav = float(balance.as_double())
        quantity_value, planned_loss = risk_sized_quantity(
            nav=nav,
            risk_fraction=float(self.config.risk_fraction),
            expected_loss_per_unit=expected_loss,
            size_increment=size_increment,
        )
        if quantity_value <= 0:
            self._skip_setup(setup, "QUANTITY_ROUNDED_TO_ZERO", bar, {"nav": nav})
            self.logic.mark_trade(setup.signal_index)
            return

        quantity = self.instrument.make_qty(Decimal(str(quantity_value)))
        min_qty = self.instrument.min_quantity
        if min_qty is not None and quantity < min_qty:
            self._skip_setup(setup, "BELOW_VENUE_MINIMUM_QUANTITY", bar, {"quantity": quantity_value})
            self.logic.mark_trade(setup.signal_index)
            return
        min_notional = self.instrument.min_notional
        if min_notional is not None and quantity.as_double() * entry_estimate < min_notional.as_double():
            self._skip_setup(setup, "BELOW_VENUE_MINIMUM_NOTIONAL", bar, {"quantity": quantity_value})
            self.logic.mark_trade(setup.signal_index)
            return

        stop_price = self.instrument.make_price(Decimal(str(stop_value)))
        target_price = self.instrument.make_price(Decimal(str(target_value)))
        orders = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            tp_price=target_price,
            tp_post_only=False,
            sl_trigger_price=stop_price,
            entry_tags=[setup.scenario_id, setup.family.value, setup.direction.value, "ENTRY"],
            tp_tags=[setup.scenario_id, setup.family.value, "TARGET"],
            sl_tags=[setup.scenario_id, setup.family.value, "STOP"],
        )
        entry_order, stop_order, target_order = orders.orders
        self.current_scenario_id = setup.scenario_id
        self.current_family = setup.family.value
        self.current_direction = setup.direction.value
        self.current_entry_order_id = str(entry_order.client_order_id)
        self.current_exit_order_ids = {
            str(stop_order.client_order_id),
            str(target_order.client_order_id),
        }
        self.current_scenario_state = "CONFIRMED"
        self.entry_inflight = True
        self.trade_intents.append(
            {
                "scenario_id": setup.scenario_id,
                "scenario_family": setup.family.value,
                "direction": setup.direction.value,
                "signal_index": setup.signal_index,
                "signal_time_ns": setup.signal_time_ns,
                "pool_id": setup.pool_id,
                "pool_level": setup.pool_level,
                "estimated_entry": entry_estimate,
                "structural_stop": stop_value,
                "liquidity_target": target_value,
                "quantity": float(quantity.as_double()),
                "nav_at_signal": nav,
                "risk_fraction": float(self.config.risk_fraction),
                "risk_budget": nav * float(self.config.risk_fraction),
                "planned_stop_loss": planned_loss,
                "expected_loss_per_unit": expected_loss,
                "expected_gain_per_unit": expected_gain,
                "net_reward_risk": net_rr,
                "entry_order_id": str(entry_order.client_order_id),
                "stop_order_id": str(stop_order.client_order_id),
                "target_order_id": str(target_order.client_order_id),
                "logic_details": setup.details,
            }
        )
        self._transition(
            setup.scenario_id,
            event_type="BRACKET_SUBMITTED",
            observed_time_ns=bar.ts_event_ns,
            next_state="ORDER_SUBMITTED",
            reason_code="NAV_RISK_SIZED_OUO_BRACKET",
            reference_price=entry_estimate,
            details={
                "quantity": float(quantity.as_double()),
                "planned_loss": planned_loss,
                "net_reward_risk": net_rr,
            },
        )
        self.submit_order_list(orders)
        self.logic.mark_trade(setup.signal_index)

    def _request_exit(self, reason: str, bar: BarPoint) -> None:
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
                observed_time_ns=bar.ts_event_ns,
                next_state="EXIT_REQUESTED",
                reason_code=reason,
                reference_price=bar.close,
            )

    def on_order_filled(self, event: Any) -> None:
        order_id = str(event.client_order_id)
        self.last_fill_order_id = order_id
        if order_id == self.current_entry_order_id:
            self.entry_inflight = False

    def on_position_opened(self, event: Any) -> None:
        self.position_open_index = self.bar_index
        self.entry_inflight = False
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                event_type="POSITION_OPENED",
                observed_time_ns=int(event.ts_event),
                next_state="POSITION_OPEN",
                reason_code="ENTRY_FILLED",
                reference_price=float(event.avg_px_open),
                details={"position_id": str(event.position_id)},
            )

    def on_position_closed(self, event: Any) -> None:
        reason = self.exit_request_reason
        if reason is None:
            if self.last_fill_order_id in self.current_exit_order_ids:
                reason = "BRACKET_EXIT"
            else:
                reason = "UNEXPECTED_CLOSE_OR_LIQUIDATION"
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
        self.position_open_index = None
        self.current_scenario_id = None
        self.current_family = None
        self.current_direction = None
        self.current_entry_order_id = None
        self.current_exit_order_ids.clear()
        self.current_scenario_state = None
        self.entry_inflight = False
        self.exit_requested = False
        self.exit_request_reason = None
        self.last_fill_order_id = None

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
        if self.current_scenario_id is not None:
            if not self.portfolio.is_flat(self.config.instrument_id):
                self.cancel_all_orders(self.config.instrument_id)
                self.close_all_positions(self.config.instrument_id)
                next_state = "EXIT_REQUESTED"
                self.exit_requested = True
                self.exit_request_reason = reason
            else:
                next_state = "FAILED"
                self.entry_inflight = False
            self._transition(
                self.current_scenario_id,
                event_type=reason,
                observed_time_ns=int(event.ts_event),
                next_state=next_state,
                reason_code=reason,
                details=record,
            )

    def on_stop(self) -> None:
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)

    def _skip_setup(
        self,
        setup: TradeSetup,
        reason: str,
        bar: BarPoint,
        details: dict[str, Any],
    ) -> None:
        record = {
            "scenario_id": setup.scenario_id,
            "scenario_family": setup.family.value,
            "direction": setup.direction.value,
            "signal_time_ns": setup.signal_time_ns,
            "reason": reason,
            **details,
        }
        self.skipped_setups.append(record)
        self.current_scenario_id = setup.scenario_id
        self.current_scenario_state = "CONFIRMED"
        self._transition(
            setup.scenario_id,
            event_type="SETUP_SKIPPED",
            observed_time_ns=bar.ts_event_ns,
            next_state="SKIPPED",
            reason_code=reason,
            reference_price=bar.close,
            details=details,
        )
        self.current_scenario_id = None
        self.current_scenario_state = None

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
        self.logic.events.append(
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

    def _round_price(self, value: float, rounding: str) -> float:
        assert self.instrument is not None
        tick = self.instrument.price_increment.as_decimal()
        units = (Decimal(str(value)) / tick).to_integral_value(rounding=rounding)
        return float(units * tick)
