"""Source-explicit half-profit then breakeven management for EasyChart v12.

The supplied scalp lesson states: when the first structural high/low is taken,
realize half, move the remaining stop to average entry, and let the rest run.
The existing plan target is already the nearest pre-existing opposite structure,
so it is used as that first objective. The remainder receives no invented price
target; it remains protected at actual average entry and exits through the
source-explicit 24-hour day-trade horizon.

Initial protection remains a native Nautilus bracket. After the real entry is
fully filled, only its linked take-profit child is resized to the exchange-valid
half quantity. When that child is fully filled, the linked original stop is
canceled by the bracket's OUO relationship and a new full-remainder breakeven
stop is submitted immediately. Any order failure invokes the existing fail-
closed emergency exit path.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce, TriggerType
from nautilus_trader.model.events import OrderFilled, PositionClosed
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.orders.list import OrderList

from domain import Side
from mtf_strategy_daily_risk_v11 import (
    DAILY_BOUNDARY_TRANSLATION,
    DAILY_LOSS_CAP_FRACTION,
    DAILY_RISK_PROVENANCE,
    DailyRiskDayTradeStrategy,
)
from partial_management_smoke_v12 import split_half


HALF_BE_PROVENANCE = (
    "SOURCE_EXPLICIT:"
    "FIRST_OPPOSING_STRUCTURE_TAKES_HALF_AND_REMAINDER_STOP_MOVES_TO_AVERAGE_ENTRY"
)
RUNNER_EXIT_PROVENANCE = (
    "SOURCE_EXPLICIT_COMBINATION:"
    "BREAKEVEN_REMAINDER_RUNS_UNTIL_THE_DAY_TRADE_24H_TERMINAL_HORIZON"
)


class HalfThenBreakevenStrategy(DailyRiskDayTradeStrategy):
    """Daily-risk policy with native half-target and breakeven runner."""

    def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
        super().__init__(config)
        self._initial_stop_id: ClientOrderId | None = None
        self._first_target_id: ClientOrderId | None = None
        self._first_target_order = None
        self._breakeven_stop_id: ClientOrderId | None = None
        self._first_leg_qty: Decimal | None = None
        self._runner_qty: Decimal | None = None
        self._actual_average_entry: Decimal | None = None
        self._first_target_complete = False

    def _reset_partial_management(self) -> None:
        self._initial_stop_id = None
        self._first_target_id = None
        self._first_target_order = None
        self._breakeven_stop_id = None
        self._first_leg_qty = None
        self._runner_qty = None
        self._actual_average_entry = None
        self._first_target_complete = False

    def _submit_plan(self, instrument_id: InstrumentId, plan: Any) -> bool:
        self._ensure_daily_session(plan.observed_time_ns)
        instrument = self.instruments[instrument_id]
        nav = self._current_nav()
        budget = self._remaining_daily_budget(nav)
        if budget <= 0:
            self._record(
                "plan_rejected_daily_loss_cap",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                nav_at_submission=float(nav),
                day_start_nav=float(self._daily_start_nav),
                cumulative_realized_losses=float(self._daily_realized_losses),
                daily_loss_cap_fraction=float(DAILY_LOSS_CAP_FRACTION),
                provenance=DAILY_RISK_PROVENANCE,
            )
            return False

        entry_slippage, stop_slippage = self._execution_reserves(instrument)
        quantity = self._quantity_for_budget(instrument, plan, budget)
        if quantity is None:
            self._record(
                "plan_rejected_daily_budget_quantity",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                nav_at_submission=float(nav),
                day_start_nav=float(self._daily_start_nav),
                cumulative_realized_losses=float(self._daily_realized_losses),
                remaining_daily_risk_budget=float(budget),
                estimated_entry_slippage=float(entry_slippage),
                estimated_stop_slippage=float(stop_slippage),
                provenance=DAILY_RISK_PROVENANCE,
            )
            return False

        try:
            first_leg, runner = split_half(
                quantity.as_decimal(),
                instrument.size_increment.as_decimal(),
            )
        except ValueError as exc:
            self._record(
                "plan_rejected_unsplittable_quantity",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                quantity=str(quantity),
                reason=str(exc),
                provenance=HALF_BE_PROVENANCE,
            )
            return False
        minimum = Decimal(str(instrument.min_quantity))
        if first_leg < minimum or runner < minimum:
            self._record(
                "plan_rejected_partial_leg_below_minimum",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                first_leg_qty=str(first_leg),
                runner_qty=str(runner),
                min_quantity=str(minimum),
                provenance=HALF_BE_PROVENANCE,
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
            entry_order_type=OrderType.MARKET,
            entry_post_only=False,
            tp_post_only=False,
            emulation_trigger=TriggerType.NO_TRIGGER,
            entry_tags=[plan_tag, "ROLE:ENTRY"],
            sl_tags=[plan_tag, "ROLE:INITIAL_STOP"],
            tp_tags=[plan_tag, "ROLE:FIRST_HALF_TARGET"],
        )
        self._reset_partial_management()
        self._initial_stop_id = order_list.orders[1].client_order_id
        self._first_target_id = order_list.orders[2].client_order_id
        self._first_target_order = order_list.orders[2]
        self._first_leg_qty = first_leg
        self._runner_qty = runner
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
            initial_stop_client_order_id=str(self._initial_stop_id),
            first_target_client_order_id=str(self._first_target_id),
            first_leg_qty=str(first_leg),
            runner_qty=str(runner),
            first_objective=plan.target,
            nav_at_submission=float(nav),
            risk_budget=float(budget),
            configured_per_trade_risk_fraction=float(self.config.risk_fraction),
            daily_loss_cap_fraction=float(DAILY_LOSS_CAP_FRACTION),
            day_start_nav=float(self._daily_start_nav),
            cumulative_realized_losses=float(self._daily_realized_losses),
            remaining_daily_risk_budget_before_submission=float(budget),
            estimated_entry_slippage=float(entry_slippage),
            estimated_stop_slippage=float(stop_slippage),
            management_provenance=HALF_BE_PROVENANCE,
            runner_exit_provenance=RUNNER_EXIT_PROVENANCE,
            daily_risk_provenance=DAILY_RISK_PROVENANCE,
            daily_boundary_translation=DAILY_BOUNDARY_TRANSLATION,
        )
        return True

    def _entry_is_complete(self) -> bool:
        if self.active_entry_id is None:
            return False
        order = self.cache.order(self.active_entry_id)
        return bool(order is not None and order.is_filled)

    def _first_target_is_complete(self) -> bool:
        if self._first_target_id is None:
            return False
        order = self.cache.order(self._first_target_id)
        return bool(order is not None and order.is_filled)

    def _actual_entry_from_position(self) -> Decimal:
        if self.active_instrument_id is None:
            raise RuntimeError("active instrument missing")
        positions = self.cache.positions_open(
            instrument_id=self.active_instrument_id,
            strategy_id=self.id,
        )
        if len(positions) != 1:
            raise RuntimeError(f"expected one live position, found {len(positions)}")
        return Decimal(str(positions[0].avg_px_open))

    def _install_breakeven_stop(self, event: OrderFilled) -> None:
        if self.active_plan is None or self.active_instrument_id is None:
            raise RuntimeError("first target completed without active plan")
        positions = self.cache.positions_open(
            instrument_id=self.active_instrument_id,
            strategy_id=self.id,
        )
        if len(positions) != 1:
            raise RuntimeError(f"expected one runner position, found {len(positions)}")
        position = positions[0]
        live_qty = position.quantity.as_decimal()
        if self._runner_qty is None or live_qty != self._runner_qty:
            raise RuntimeError(f"runner quantity mismatch: {live_qty} != {self._runner_qty}")
        if self._actual_average_entry is None:
            self._actual_average_entry = Decimal(str(position.avg_px_open))
        instrument = self.instruments[self.active_instrument_id]
        side = OrderSide.SELL if self.active_plan.side is Side.LONG else OrderSide.BUY
        plan_tag = f"PLAN:{self.active_plan.plan_id}"
        order = self.order_factory.stop_market(
            instrument_id=self.active_instrument_id,
            order_side=side,
            quantity=instrument.make_qty(live_qty),
            trigger_price=instrument.make_price(self._actual_average_entry),
            trigger_type=TriggerType.LAST_PRICE,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            tags=[
                plan_tag,
                "ROLE:BREAKEVEN_RUNNER_STOP",
                HALF_BE_PROVENANCE,
            ],
        )
        self._breakeven_stop_id = order.client_order_id
        self.submit_order(order, position_id=position.id)
        self._record(
            "breakeven_runner_stop_submitted",
            plan_id=self.active_plan.plan_id,
            instrument_id=str(self.active_instrument_id),
            client_order_id=str(order.client_order_id),
            first_target_fill_price=str(event.last_px),
            first_target_fill_time_ns=event.ts_event,
            runner_qty=str(live_qty),
            breakeven_trigger=str(self._actual_average_entry),
            provenance=HALF_BE_PROVENANCE,
        )

    def on_order_filled(self, event: OrderFilled) -> None:
        is_entry = self.active_entry_id is not None and event.client_order_id == self.active_entry_id
        is_first_target = (
            self._first_target_id is not None
            and event.client_order_id == self._first_target_id
        )
        is_breakeven = (
            self._breakeven_stop_id is not None
            and event.client_order_id == self._breakeven_stop_id
        )
        super().on_order_filled(event)

        if is_entry and self._actual_average_entry is None and self._entry_is_complete():
            if self._first_target_order is None or self._first_leg_qty is None:
                raise RuntimeError("partial-management target state missing")
            self._actual_average_entry = self._actual_entry_from_position()
            instrument = self.instruments[event.instrument_id]
            self.modify_order(
                self._first_target_order,
                quantity=instrument.make_qty(self._first_leg_qty),
            )
            self._record(
                "first_target_resize_requested",
                plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                instrument_id=str(event.instrument_id),
                first_leg_qty=str(self._first_leg_qty),
                runner_qty=str(self._runner_qty),
                actual_average_entry=str(self._actual_average_entry),
                provenance=HALF_BE_PROVENANCE,
            )

        if is_first_target and not self._first_target_complete and self._first_target_is_complete():
            self._first_target_complete = True
            self._record(
                "first_half_target_completed",
                plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                instrument_id=str(event.instrument_id),
                fill_price=str(event.last_px),
                fill_time_ns=event.ts_event,
                provenance=HALF_BE_PROVENANCE,
            )
            self._install_breakeven_stop(event)

        if is_breakeven:
            self._record(
                "breakeven_runner_stop_filled",
                plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                instrument_id=str(event.instrument_id),
                fill_price=str(event.last_px),
                fill_time_ns=event.ts_event,
                provenance=HALF_BE_PROVENANCE,
            )

    def on_position_closed(self, event: PositionClosed) -> None:
        super().on_position_closed(event)
        self._reset_partial_management()

    def on_stop(self) -> None:
        super().on_stop()
        self._reset_partial_management()
