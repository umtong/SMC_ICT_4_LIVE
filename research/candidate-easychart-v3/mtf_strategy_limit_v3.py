"""NautilusTrader limit-entry binding for EasyChart v3."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import mtf_strategy as _base
from limit_scenario_v3 import LimitResearchScenarioBundle
from model import Side
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce, TriggerType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.orders.list import OrderList

_base.MultiScaleScenarioBundle = LimitResearchScenarioBundle
EasyChartMTFConfig = _base.EasyChartMTFConfig


class EasyChartMTFStrategy(_base.EasyChartMTFStrategy):
    """One global slot; native GTC limit bracket at the predeclared zone."""

    def __init__(self, config: EasyChartMTFConfig) -> None:
        super().__init__(config)
        self._last_execution_bar: dict[InstrumentId, Bar] = {}

    def _submit_plan(self, instrument_id: InstrumentId, plan: Any) -> bool:
        instrument = self.instruments[instrument_id]
        nav = self._current_nav()
        entry_slippage, stop_slippage = self._execution_reserves(instrument)
        quantity = self._quantity(instrument, plan, nav)
        if quantity is None:
            self._record(
                "plan_rejected_quantity",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                nav_at_submission=float(nav),
                estimated_entry_slippage=float(entry_slippage),
                estimated_stop_slippage=float(stop_slippage),
            )
            return False
        plan_tag = f"PLAN:{plan.plan_id}"
        order_list: OrderList = self.order_factory.bracket(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY if plan.side is Side.LONG else OrderSide.SELL,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            entry_order_type=OrderType.LIMIT,
            entry_price=instrument.make_price(plan.entry),
            entry_post_only=False,
            sl_trigger_price=instrument.make_price(plan.stop),
            tp_price=instrument.make_price(plan.target),
            tp_post_only=False,
            emulation_trigger=TriggerType.NO_TRIGGER,
            entry_tags=[plan_tag, "ROLE:ENTRY", "POLICY:PLANNED_ZONE_LIMIT"],
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
            planned_entry=plan.entry,
            entry_policy="PLANNED_ZONE_LIMIT_GTC",
            nav_at_submission=float(nav),
            risk_budget=float(nav * Decimal(str(self.config.risk_fraction))),
            estimated_entry_slippage=float(entry_slippage),
            estimated_stop_slippage=float(stop_slippage),
        )
        return True

    def _cancel_resolved_unfilled_parent(self, instrument_id: InstrumentId, previous: Bar) -> None:
        if (
            self.active_plan is None
            or self.active_instrument_id != instrument_id
            or self.active_entry_id is None
            or self.entry_cancel_requested
        ):
            return
        order = self.cache.order(self.active_entry_id)
        if order is None or not order.is_open:
            return
        # Once any quantity has become a real position, the native bracket owns
        # protection.  Never cancel a partially filled parent by pretending it
        # is still a purely hypothetical setup.
        if not self.portfolio.is_flat(instrument_id):
            return
        plan = self.active_plan
        if previous.ts_event <= plan.observed_time_ns:
            return
        if plan.side is Side.LONG:
            invalidated = float(previous.low) <= plan.stop
            objective_spent = float(previous.high) >= plan.target
        else:
            invalidated = float(previous.high) >= plan.stop
            objective_spent = float(previous.low) <= plan.target
        if not (invalidated or objective_spent):
            return
        reason = "PRE_ENTRY_INVALIDATION" if invalidated else "PRE_ENTRY_TARGET_SPENT"
        self.entry_cancel_requested = True
        self.cancel_order(order)
        self._record(
            "pending_limit_cancel_requested",
            plan_id=plan.plan_id,
            instrument_id=str(instrument_id),
            client_order_id=str(self.active_entry_id),
            reason=reason,
            evidence_bar_ts_ns=previous.ts_event,
            evidence_high=float(previous.high),
            evidence_low=float(previous.low),
        )

    def on_bar(self, bar: Bar) -> None:
        route = self.route_by_key.get(bar.bar_type.id_spec_key())
        if route is not None:
            instrument_id, timeframe = route
            if timeframe == self.EXECUTION_MINUTES:
                previous = self._last_execution_bar.get(instrument_id)
                if previous is not None:
                    # Evaluate the fully completed previous minute. This avoids
                    # assuming whether the matching engine processes a pending
                    # order before or after the strategy callback for this bar.
                    self._cancel_resolved_unfilled_parent(instrument_id, previous)
                self._last_execution_bar[instrument_id] = bar
        super().on_bar(bar)
