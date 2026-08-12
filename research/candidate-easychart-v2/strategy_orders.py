"""Risk sizing, native bracket submission and terminal event handling."""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce, TriggerType
from nautilus_trader.model.events import (
    OrderCanceled,
    OrderDenied,
    OrderExpired,
    OrderFilled,
    OrderRejected,
    PositionClosed,
)
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId, Venue
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.orders.list import OrderList

from model import Side, TradePlan


class EasyChartOrderMixin:
    def _current_nav(self) -> Decimal:
        account = self.portfolio.account(Venue("BINANCE"))
        if account is None:
            raise RuntimeError("BINANCE account unavailable")
        money = account.balance_total(Currency.from_str("USDT"))
        if money is None:
            raise RuntimeError("USDT balance unavailable")
        return Decimal(str(money.as_double()))

    def _execution_reserves(self, instrument: Any) -> tuple[Decimal, Decimal]:
        tick = Decimal(str(instrument.price_increment))
        entry = tick * Decimal(self.config.estimated_entry_slippage_ticks)
        stop = tick * Decimal(self.config.estimated_stop_slippage_ticks)
        return entry, stop

    def _estimated_geometry(self, instrument: Any, plan: Any) -> dict[str, Decimal]:
        """Return fill-aware loss and target geometry known before submission."""
        entry = Decimal(str(plan.entry))
        stop = Decimal(str(plan.stop))
        target = Decimal(str(plan.target))
        direction = Decimal(int(plan.side.value))
        entry_slippage, stop_slippage = self._execution_reserves(instrument)
        expected_entry = entry + direction * entry_slippage
        expected_stop = stop - direction * stop_slippage
        entry_fee_rate = Decimal(str(self.config.estimated_entry_fee_rate))
        stop_fee_rate = Decimal(str(self.config.estimated_stop_fee_rate))
        target_fee_rate = Decimal(
            str(getattr(self.config, "estimated_target_fee_rate", self.config.estimated_entry_fee_rate)),
        )
        funding_rate = Decimal(str(self.config.estimated_funding_rate))

        planned_loss_per_unit = abs(expected_entry - expected_stop)
        planned_loss_per_unit += expected_entry * entry_fee_rate
        planned_loss_per_unit += expected_stop * stop_fee_rate
        planned_loss_per_unit += expected_entry * funding_rate

        gross_reward_per_unit = direction * (target - expected_entry)
        net_reward_per_unit = gross_reward_per_unit
        net_reward_per_unit -= expected_entry * entry_fee_rate
        net_reward_per_unit -= target * target_fee_rate
        net_reward_per_unit -= expected_entry * funding_rate
        cost_after_rr = (
            net_reward_per_unit / planned_loss_per_unit
            if planned_loss_per_unit > 0
            else Decimal("-Infinity")
        )
        return {
            "expected_entry": expected_entry,
            "expected_stop_fill": expected_stop,
            "planned_loss_per_unit": planned_loss_per_unit,
            "gross_reward_per_unit": gross_reward_per_unit,
            "net_reward_per_unit": net_reward_per_unit,
            "cost_after_rr": cost_after_rr,
            "entry_slippage": entry_slippage,
            "stop_slippage": stop_slippage,
        }

    def _quantity(
        self,
        instrument: Any,
        nav: Decimal,
        geometry: dict[str, Decimal],
    ) -> Any | None:
        per_unit = geometry["planned_loss_per_unit"]
        if per_unit <= 0:
            return None
        raw = nav * Decimal(str(self.config.risk_fraction)) / per_unit
        step = Decimal(str(instrument.size_increment))
        floored = (raw / step).to_integral_value(rounding=ROUND_DOWN) * step
        minimum = Decimal(str(instrument.min_quantity))
        maximum = Decimal(str(instrument.max_quantity)) if instrument.max_quantity is not None else None
        if floored <= 0 or floored < minimum:
            return None
        if maximum is not None and floored > maximum:
            return None
        return instrument.make_qty(floored)

    def _submit_plan(self, instrument_id: InstrumentId, plan: Any) -> bool:
        instrument = self.instruments[instrument_id]
        nav = self._current_nav()
        geometry = self._estimated_geometry(instrument, plan)
        if geometry["gross_reward_per_unit"] <= 0 or geometry["net_reward_per_unit"] <= 0:
            self._record(
                "plan_rejected_nonpositive_cost_after_target",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                nav_at_submission=float(nav),
                gross_reward_per_unit=float(geometry["gross_reward_per_unit"]),
                estimated_net_reward_per_unit=float(geometry["net_reward_per_unit"]),
                estimated_cost_after_rr=float(geometry["cost_after_rr"]),
            )
            return False
        quantity = self._quantity(instrument, nav, geometry)
        if quantity is None:
            self._record(
                "plan_rejected_quantity",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                nav_at_submission=float(nav),
                estimated_entry_slippage=float(geometry["entry_slippage"]),
                estimated_stop_slippage=float(geometry["stop_slippage"]),
                estimated_cost_after_rr=float(geometry["cost_after_rr"]),
            )
            return False
        plan_tag = f"PLAN:{plan.plan_id}"
        order_list: OrderList = self.order_factory.bracket(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY if plan.side is Side.LONG else OrderSide.SELL,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            sl_trigger_price=instrument.make_price(plan.stop),
            tp_price=instrument.make_price(plan.target),
            # The scenario is confirmed on a closed 5m bar, so the next executable
            # action is one market entry. NautilusTrader owns the native bracket,
            # fills, stop, target, fees, position and account state.
            entry_order_type=OrderType.MARKET,
            entry_post_only=False,
            tp_post_only=False,
            emulation_trigger=TriggerType.NO_TRIGGER,
            entry_tags=[plan_tag, "ROLE:ENTRY"],
            sl_tags=[plan_tag, "ROLE:STOP_LOSS"],
            tp_tags=[plan_tag, "ROLE:TAKE_PROFIT"],
        )
        self.active_plan = plan
        self.active_instrument_id = instrument_id
        self.active_entry_id = order_list.first.client_order_id
        self.entry_cancel_requested = False
        self.emergency_exit_requested = False
        self.submit_order_list(order_list)
        self._record(
            "submitted",
            plan_id=plan.plan_id,
            instrument_id=str(instrument_id),
            quantity=str(quantity),
            entry_client_order_id=str(self.active_entry_id),
            nav_at_submission=float(nav),
            risk_budget=float(nav * Decimal(str(self.config.risk_fraction))),
            estimated_entry_price=float(geometry["expected_entry"]),
            estimated_stop_fill=float(geometry["expected_stop_fill"]),
            estimated_entry_slippage=float(geometry["entry_slippage"]),
            estimated_stop_slippage=float(geometry["stop_slippage"]),
            estimated_planned_loss_per_unit=float(geometry["planned_loss_per_unit"]),
            estimated_net_reward_per_unit=float(geometry["net_reward_per_unit"]),
            estimated_cost_after_rr=float(geometry["cost_after_rr"]),
        )
        return True

    def _clear_if_entry(self, client_order_id: ClientOrderId) -> bool:
        if self.active_entry_id is None or client_order_id != self.active_entry_id:
            return False
        plan_id = self.active_plan.plan_id if self.active_plan else None
        self._record("entry_terminal_without_position", plan_id=plan_id)
        self.active_plan = None
        self.active_instrument_id = None
        self.active_entry_id = None
        self.entry_cancel_requested = False
        self.emergency_exit_requested = False
        return True

    def _protective_failure(self, client_order_id: ClientOrderId, reason: str) -> None:
        if self.active_plan is None or self.active_instrument_id is None:
            return
        if client_order_id == self.active_entry_id or self.emergency_exit_requested:
            return
        if not self.portfolio.is_flat(self.active_instrument_id):
            self.emergency_exit_requested = True
            self._record(
                "emergency_exit_protective_failure",
                plan_id=self.active_plan.plan_id,
                client_order_id=str(client_order_id),
                reason=reason,
            )
            self.cancel_all_orders(self.active_instrument_id)
            self.close_all_positions(self.active_instrument_id)

    def on_order_filled(self, event: OrderFilled) -> None:
        plan_id = self.active_plan.plan_id if self.active_plan else None
        role = "ENTRY" if event.client_order_id == self.active_entry_id else "EXIT_OR_PROTECTIVE"
        self._record(
            "order_filled",
            plan_id=plan_id,
            role=role,
            client_order_id=str(event.client_order_id),
            venue_order_id=None if event.venue_order_id is None else str(event.venue_order_id),
            position_id=None if event.position_id is None else str(event.position_id),
            instrument_id=str(event.instrument_id),
            order_side=str(event.order_side),
            order_type=str(event.order_type),
            last_qty=str(event.last_qty),
            last_px=str(event.last_px),
            commission=str(event.commission),
            liquidity_side=str(event.liquidity_side),
            event_ts_ns=event.ts_event,
        )

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self._clear_if_entry(event.client_order_id)

    def on_order_expired(self, event: OrderExpired) -> None:
        self._clear_if_entry(event.client_order_id)

    def on_order_rejected(self, event: OrderRejected) -> None:
        reason = str(event.reason)
        self._record("order_rejected", client_order_id=str(event.client_order_id), reason=reason)
        if not self._clear_if_entry(event.client_order_id):
            self._protective_failure(event.client_order_id, reason)

    def on_order_denied(self, event: OrderDenied) -> None:
        reason = str(event.reason)
        self._record("order_denied", client_order_id=str(event.client_order_id), reason=reason)
        if not self._clear_if_entry(event.client_order_id):
            self._protective_failure(event.client_order_id, reason)

    def on_position_closed(self, event: PositionClosed) -> None:
        plan_id = self.active_plan.plan_id if self.active_plan else None
        self._record("position_closed", plan_id=plan_id, instrument_id=str(event.instrument_id))
        self.active_plan = None
        self.active_instrument_id = None
        self.active_entry_id = None
        self.entry_cancel_requested = False
        self.emergency_exit_requested = False

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
