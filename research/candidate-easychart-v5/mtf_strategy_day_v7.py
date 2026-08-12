"""Source-aligned day-trading lifecycle for EasyChart v7.

The supplied Korean material explicitly defines day trading as a trade completed
within one day.  Earlier candidates allowed an opposing structure target to keep
a position open for several days; that silently changed the source's operating
horizon and concentrated the account result in a few long holds.

This binding keeps the already-audited native bracket and adds exactly one full
position exit at 24 hours from the first real entry fill.  The alert is scheduled
by the NautilusTrader clock, so the same causal event and execution path operate
in backtest and live trading.  It is not a profit-taking score or a daily loss
limit: stop and target remain live until the source-explicit day-trading horizon
expires.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from nautilus_trader.common import TimeEvent
from nautilus_trader.model.enums import OrderSide, PositionSide, TimeInForce
from nautilus_trader.model.events import OrderFilled, PositionClosed
from nautilus_trader.model.identifiers import ClientOrderId

import mtf_strategy_v5 as _base

EasyChartMTFConfig = _base.EasyChartMTFConfig


NS_PER_HOUR = 3_600_000_000_000
MAX_HOLD_HOURS = 24
MAX_HOLD_NS = MAX_HOLD_HOURS * NS_PER_HOUR
MAX_HOLD_PROVENANCE = "SOURCE_EXPLICIT:DAY_TRADING_POSITION_COMPLETES_WITHIN_ONE_DAY"


def max_hold_deadline_ns(first_fill_ts_ns: int) -> int:
    if first_fill_ts_ns < 0:
        raise ValueError("first fill timestamp cannot be negative")
    return first_fill_ts_ns + MAX_HOLD_NS


def closing_order_side(position_side: PositionSide) -> OrderSide:
    if position_side is PositionSide.LONG:
        return OrderSide.SELL
    if position_side is PositionSide.SHORT:
        return OrderSide.BUY
    raise ValueError(f"cannot close flat position side {position_side}")


class EasyChartDayTradeStrategy(_base.EasyChartMTFStrategy):
    """One native bracket plus a full source-aligned 24-hour terminal exit."""

    def __init__(self, config: EasyChartMTFConfig) -> None:
        super().__init__(config)
        self._first_entry_fill_ts_ns: int | None = None
        self._max_hold_deadline_ns: int | None = None
        self._max_hold_alert_name: str | None = None
        self._time_exit_requested = False
        self._time_exit_order_id: ClientOrderId | None = None

    def _reset_daytrade_lifecycle(self) -> None:
        if self._max_hold_alert_name is not None:
            self.clock.cancel_timer(self._max_hold_alert_name)
        self._first_entry_fill_ts_ns = None
        self._max_hold_deadline_ns = None
        self._max_hold_alert_name = None
        self._time_exit_requested = False
        self._time_exit_order_id = None

    def _schedule_max_hold(self, first_fill_ts_ns: int) -> None:
        if self.active_plan is None:
            raise RuntimeError("entry fill cannot schedule day-trade expiry without an active plan")
        if self._max_hold_alert_name is not None:
            return
        deadline_ns = max_hold_deadline_ns(first_fill_ts_ns)
        alert_name = f"{self.id}.max-hold"
        deadline = datetime.fromtimestamp(deadline_ns / 1_000_000_000, tz=UTC)
        self.clock.set_time_alert(
            alert_name,
            deadline,
            callback=self._on_max_hold_alert,
        )
        self._first_entry_fill_ts_ns = first_fill_ts_ns
        self._max_hold_deadline_ns = deadline_ns
        self._max_hold_alert_name = alert_name
        self._record(
            "max_hold_scheduled",
            plan_id=self.active_plan.plan_id,
            first_entry_fill_ts_ns=first_fill_ts_ns,
            max_hold_deadline_ns=deadline_ns,
            max_hold_hours=MAX_HOLD_HOURS,
            provenance=MAX_HOLD_PROVENANCE,
        )

    def _on_max_hold_alert(self, event: TimeEvent) -> None:
        if self.active_plan is None or self.active_instrument_id is None:
            return
        if self._time_exit_requested or self.portfolio.is_flat(self.active_instrument_id):
            return

        positions = self.cache.positions_open(
            instrument_id=self.active_instrument_id,
            strategy_id=self.id,
        )
        if len(positions) != 1:
            # This is a state-reconciliation failure, not a trading decision.
            # Fail closed in the controlled single-strategy account rather than
            # silently letting a position exceed the day-trading contract.
            self._record(
                "emergency_exit_time_limit_state_mismatch",
                plan_id=self.active_plan.plan_id,
                instrument_id=str(self.active_instrument_id),
                open_strategy_positions=len(positions),
                alert_ts_ns=event.ts_event,
            )
            self.emergency_exit_requested = True
            self.cancel_all_orders(self.active_instrument_id)
            self.close_all_positions(self.active_instrument_id)
            return

        position = positions[0]
        plan_tag = f"PLAN:{self.active_plan.plan_id}"
        order = self.order_factory.market(
            instrument_id=position.instrument_id,
            order_side=closing_order_side(position.side),
            quantity=position.quantity,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            tags=[
                plan_tag,
                "ROLE:TIME_EXIT",
                "POLICY:MAX_24H_HOLD",
                MAX_HOLD_PROVENANCE,
            ],
        )
        self._time_exit_requested = True
        self._time_exit_order_id = order.client_order_id
        self.cancel_all_orders(self.active_instrument_id)
        self.submit_order(order, position_id=position.id)
        first_fill = self._first_entry_fill_ts_ns
        self._record(
            "time_exit_submitted",
            plan_id=self.active_plan.plan_id,
            instrument_id=str(self.active_instrument_id),
            position_id=str(position.id),
            client_order_id=str(order.client_order_id),
            quantity=str(position.quantity),
            alert_ts_ns=event.ts_event,
            first_entry_fill_ts_ns=first_fill,
            actual_age_ns=None if first_fill is None else event.ts_event - first_fill,
            max_hold_deadline_ns=self._max_hold_deadline_ns,
            max_hold_hours=MAX_HOLD_HOURS,
            provenance=MAX_HOLD_PROVENANCE,
        )

    def on_order_filled(self, event: OrderFilled) -> None:
        is_entry = self.active_entry_id is not None and event.client_order_id == self.active_entry_id
        is_time_exit = (
            self._time_exit_order_id is not None
            and event.client_order_id == self._time_exit_order_id
        )
        super().on_order_filled(event)
        if is_entry and self._first_entry_fill_ts_ns is None:
            self._schedule_max_hold(event.ts_event)
        if is_time_exit:
            self._record(
                "time_exit_filled",
                plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                client_order_id=str(event.client_order_id),
                instrument_id=str(event.instrument_id),
                last_qty=str(event.last_qty),
                last_px=str(event.last_px),
                commission=str(event.commission),
                event_ts_ns=event.ts_event,
            )

    def on_position_closed(self, event: PositionClosed) -> None:
        self._reset_daytrade_lifecycle()
        super().on_position_closed(event)

    def on_stop(self) -> None:
        self._reset_daytrade_lifecycle()
        super().on_stop()
