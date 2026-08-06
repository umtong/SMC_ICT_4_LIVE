"""Three-R structural-target protection for candidate-07.

This module adds one causal position-management transition and no replay,
matching, accounting, fee, or PnL machinery. NautilusTrader remains responsible
for the bracket order and modifies its existing stop-market child.

Once a completed one-minute close proves that an open thesis delivered +3R
from the actual average entry, the original opposing-liquidity target remains
untouched while the stop is advanced once to a cost-after break-even floor.
The position therefore continues to occupy the portfolio's single entry slot.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any

from nautilus_trader.model.data import Bar
from nautilus_trader.model.events import (
    OrderModifyRejected,
    OrderUpdated,
    PositionClosed,
    PositionOpened,
)

from model import Direction, TradePlan


PROTECTION_TRIGGER_R = Decimal("3")
_BPS_DENOMINATOR = Decimal("10000")


def favorable_progress_r(
    *,
    direction: Direction,
    entry_price: Decimal,
    initial_stop: Decimal,
    close_price: Decimal,
) -> Decimal:
    """Return completed-close favorable excursion in initial-risk units."""
    if entry_price <= 0 or initial_stop <= 0 or close_price <= 0:
        raise ValueError("prices must be positive")
    if direction is Direction.LONG:
        risk = entry_price - initial_stop
        favorable = close_price - entry_price
    else:
        risk = initial_stop - entry_price
        favorable = entry_price - close_price
    if risk <= 0:
        raise ValueError("initial stop is on the wrong side of entry")
    return favorable / risk


def cost_floor_trigger_price(
    *,
    direction: Direction,
    entry_price: Decimal,
    taker_fee_rate: Decimal,
    funding_reserve_bps: Decimal,
    price_increment: Decimal,
) -> Decimal:
    """Return a stop trigger whose adverse one-tick fill covers reserved costs.

    The actual average entry already includes entry slippage. The floor covers
    entry and stop taker fees, one adverse price increment on the stop fill,
    and the same adverse funding reserve used by position sizing.
    """
    if entry_price <= 0 or price_increment <= 0:
        raise ValueError("entry price and price increment must be positive")
    if taker_fee_rate < 0 or taker_fee_rate >= 1:
        raise ValueError("taker fee rate must be in [0, 1)")
    if funding_reserve_bps < 0:
        raise ValueError("funding reserve must be non-negative")

    funding = entry_price * funding_reserve_bps / _BPS_DENOMINATOR
    if direction is Direction.LONG:
        required_stop_fill = (
            entry_price * (Decimal(1) + taker_fee_rate) + funding
        ) / (Decimal(1) - taker_fee_rate)
        raw_trigger = required_stop_fill + price_increment
        rounding = ROUND_CEILING
    else:
        required_stop_fill = (
            entry_price * (Decimal(1) - taker_fee_rate) - funding
        ) / (Decimal(1) + taker_fee_rate)
        raw_trigger = required_stop_fill - price_increment
        rounding = ROUND_FLOOR

    units = (raw_trigger / price_increment).to_integral_value(rounding=rounding)
    trigger = units * price_increment
    if trigger <= 0:
        raise ValueError("cost floor trigger must be positive")
    return trigger


class ThreeRProgressProtectionMixin:
    """Keep the structural target while removing original risk after +3R."""

    def __init__(self, config: Any):
        super().__init__(config)
        self._progress_state = "IDLE"
        self._progress_entry: Decimal | None = None
        self._progress_initial_stop: Decimal | None = None
        self._progress_stop_order_id: Any | None = None
        self._progress_requested_trigger: Decimal | None = None
        self._progress_position_id: str | None = None

    def on_position_opened(self, event: PositionOpened) -> None:
        super().on_position_opened(event)
        if event.instrument_id != self.config.instrument_id:
            return
        plan: TradePlan | None = self._active_plan
        if plan is None:
            return

        entry = Decimal(str(event.avg_px_open))
        stop = Decimal(str(plan.stop_price))
        target = Decimal(str(plan.target_price))
        try:
            target_r = favorable_progress_r(
                direction=plan.direction,
                entry_price=entry,
                initial_stop=stop,
                close_price=target,
            )
        except ValueError:
            self._progress_state = "FAILED"
            return

        self._progress_entry = entry
        self._progress_initial_stop = stop
        self._progress_position_id = str(event.position_id)
        self._progress_stop_order_id = None
        self._progress_requested_trigger = None

        if target_r <= PROTECTION_TRIGGER_R:
            self._progress_state = "BYPASS"
            return

        self._progress_state = "ARMED"
        trigger_close = (
            entry + PROTECTION_TRIGGER_R * (entry - stop)
            if plan.direction is Direction.LONG
            else entry - PROTECTION_TRIGGER_R * (stop - entry)
        )
        self._append_manual_event(
            scenario_id=plan.scenario_id,
            previous_state="POSITION_OPEN",
            next_state="POSITION_OPEN",
            reason_code="THREE_R_COST_FLOOR_ARMED",
            event_time_ns=int(event.ts_event),
            reference_price=float(trigger_close),
            details={
                "direction": plan.direction.value,
                "actual_average_entry": float(entry),
                "initial_stop": float(stop),
                "initial_risk": float(abs(entry - stop)),
                "protection_trigger_r": float(PROTECTION_TRIGGER_R),
                "protection_trigger_close": float(trigger_close),
                "structural_target": float(target),
                "structural_target_r": float(target_r),
                "position_id": str(event.position_id),
            },
        )

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        if self._progress_state != "ARMED":
            return
        plan: TradePlan | None = self._active_plan
        if (
            plan is None
            or self._progress_entry is None
            or self._progress_initial_stop is None
            or self.portfolio.is_flat(self.config.instrument_id)
            or self._exit_pending
        ):
            return

        close = Decimal(str(bar.close.as_double()))
        try:
            progress_r = favorable_progress_r(
                direction=plan.direction,
                entry_price=self._progress_entry,
                initial_stop=self._progress_initial_stop,
                close_price=close,
            )
        except ValueError:
            self._progress_state = "FAILED"
            return
        if progress_r < PROTECTION_TRIGGER_R:
            return
        self._request_cost_floor(
            plan=plan,
            close=close,
            progress_r=progress_r,
            event_time_ns=int(bar.ts_event),
        )

    def on_order_updated(self, event: OrderUpdated) -> None:
        super().on_order_updated(event)
        if (
            self._progress_state != "REQUESTED"
            or self._progress_stop_order_id is None
            or self._progress_requested_trigger is None
            or str(event.client_order_id) != str(self._progress_stop_order_id)
            or event.trigger_price is None
        ):
            return
        trigger = self._as_decimal(event.trigger_price)
        if trigger != self._progress_requested_trigger:
            return

        self._progress_state = "ACTIVE"
        plan: TradePlan | None = self._active_plan
        if plan is not None:
            self._append_manual_event(
                scenario_id=plan.scenario_id,
                previous_state="POSITION_OPEN",
                next_state="POSITION_OPEN",
                reason_code="THREE_R_COST_FLOOR_ACTIVE",
                event_time_ns=int(event.ts_event),
                reference_price=float(trigger),
                details={
                    "stop_order_id": str(event.client_order_id),
                    "cost_floor_trigger": float(trigger),
                    "position_id": self._progress_position_id,
                },
            )

    def on_order_modify_rejected(self, event: OrderModifyRejected) -> None:
        super().on_order_modify_rejected(event)
        if (
            self._progress_state != "REQUESTED"
            or self._progress_stop_order_id is None
            or str(event.client_order_id) != str(self._progress_stop_order_id)
        ):
            return
        self._progress_state = "FAILED"
        plan: TradePlan | None = self._active_plan
        if plan is not None:
            self._append_manual_event(
                scenario_id=plan.scenario_id,
                previous_state="POSITION_OPEN",
                next_state="POSITION_OPEN",
                reason_code="THREE_R_COST_FLOOR_REJECTED",
                event_time_ns=int(event.ts_event),
                reference_price=float(plan.stop_price),
                details={
                    "stop_order_id": str(event.client_order_id),
                    "requested_trigger": (
                        float(self._progress_requested_trigger)
                        if self._progress_requested_trigger is not None
                        else None
                    ),
                    "reason": str(getattr(event, "reason", "UNKNOWN")),
                    "position_id": self._progress_position_id,
                },
            )

    def on_position_closed(self, event: PositionClosed) -> None:
        super().on_position_closed(event)
        if event.instrument_id == self.config.instrument_id:
            self._reset_progress_state()

    def _is_structural_stop(
        self,
        event: PositionClosed,
        plan: TradePlan | None,
    ) -> bool:
        if self._closed_by_active_cost_floor(event):
            return False
        return super()._is_structural_stop(event, plan)

    def _request_cost_floor(
        self,
        *,
        plan: TradePlan,
        close: Decimal,
        progress_r: Decimal,
        event_time_ns: int,
    ) -> None:
        if self._instrument is None or self._progress_entry is None:
            self._progress_state = "FAILED"
            return
        stop_order = self._find_open_stop_order()
        if stop_order is None:
            self._progress_state = "FAILED"
            self._append_manual_event(
                scenario_id=plan.scenario_id,
                previous_state="POSITION_OPEN",
                next_state="POSITION_OPEN",
                reason_code="THREE_R_COST_FLOOR_STOP_NOT_FOUND",
                event_time_ns=event_time_ns,
                reference_price=float(close),
                details={
                    "progress_r": float(progress_r),
                    "position_id": self._progress_position_id,
                },
            )
            return

        fee_rate = Decimal(str(self._instrument.taker_fee or 0))
        tick = self._instrument.price_increment.as_decimal()
        trigger = cost_floor_trigger_price(
            direction=plan.direction,
            entry_price=self._progress_entry,
            taker_fee_rate=fee_rate,
            funding_reserve_bps=Decimal(str(self.config.risk_funding_reserve_bps)),
            price_increment=tick,
        )
        if self._progress_initial_stop is None:
            self._progress_state = "FAILED"
            return
        valid = (
            self._progress_initial_stop < trigger < close
            if plan.direction is Direction.LONG
            else close < trigger < self._progress_initial_stop
        )
        if not valid:
            self._progress_state = "FAILED"
            self._append_manual_event(
                scenario_id=plan.scenario_id,
                previous_state="POSITION_OPEN",
                next_state="POSITION_OPEN",
                reason_code="THREE_R_COST_FLOOR_GEOMETRY_REJECTED",
                event_time_ns=event_time_ns,
                reference_price=float(trigger),
                details={
                    "direction": plan.direction.value,
                    "completed_close": float(close),
                    "initial_stop": float(self._progress_initial_stop),
                    "progress_r": float(progress_r),
                    "position_id": self._progress_position_id,
                },
            )
            return

        stop_order_id = stop_order.client_order_id
        trigger_price = self._instrument.make_price(trigger)
        requested = trigger_price.as_decimal()
        self._progress_stop_order_id = stop_order_id
        self._progress_requested_trigger = requested
        self._progress_state = "REQUESTED"
        self._append_manual_event(
            scenario_id=plan.scenario_id,
            previous_state="POSITION_OPEN",
            next_state="POSITION_OPEN",
            reason_code="THREE_R_COST_FLOOR_REQUESTED",
            event_time_ns=event_time_ns,
            reference_price=float(requested),
            details={
                "direction": plan.direction.value,
                "completed_close": float(close),
                "progress_r": float(progress_r),
                "actual_average_entry": float(self._progress_entry),
                "initial_stop": float(self._progress_initial_stop),
                "cost_floor_trigger": float(requested),
                "stop_order_id": str(stop_order_id),
                "position_id": self._progress_position_id,
                "taker_fee_rate": str(fee_rate),
                "funding_reserve_bps": str(self.config.risk_funding_reserve_bps),
                "adverse_stop_slippage_ticks": 1,
                "structural_target_unchanged": plan.target_price,
            },
        )
        try:
            self.modify_order(
                stop_order,
                trigger_price=trigger_price,
            )
        except Exception as exc:
            self._progress_state = "FAILED"
            self._append_manual_event(
                scenario_id=plan.scenario_id,
                previous_state="POSITION_OPEN",
                next_state="POSITION_OPEN",
                reason_code="THREE_R_COST_FLOOR_REQUEST_ERROR",
                event_time_ns=event_time_ns,
                reference_price=float(requested),
                details={
                    "stop_order_id": str(stop_order_id),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "position_id": self._progress_position_id,
                },
            )

    def _find_open_stop_order(self) -> Any | None:
        candidates: list[Any] = []
        for order in self.cache.orders_open(
            instrument_id=self.config.instrument_id,
        ):
            raw_tags = getattr(order, "tags", None) or ()
            tags = {str(tag).upper() for tag in raw_tags}
            order_type = str(getattr(order, "order_type", "")).upper()
            if "STOP_LOSS" in tags or "STOP_MARKET" in order_type:
                candidates.append(order)
        return candidates[0] if len(candidates) == 1 else None

    def _closed_by_active_cost_floor(self, event: PositionClosed) -> bool:
        if (
            self._progress_stop_order_id is None
            or self._progress_requested_trigger is None
            or str(getattr(event, "closing_order_id", ""))
            != str(self._progress_stop_order_id)
        ):
            return False
        if self._progress_state == "ACTIVE":
            return True
        if self._progress_state != "REQUESTED":
            return False
        order = self.cache.order(self._progress_stop_order_id)
        if order is None:
            return False
        actual = self._as_decimal(getattr(order, "trigger_price", None))
        return actual == self._progress_requested_trigger

    @staticmethod
    def _as_decimal(value: Any) -> Decimal | None:
        if value is None:
            return None
        if hasattr(value, "as_decimal"):
            return value.as_decimal()
        if hasattr(value, "as_double"):
            return Decimal(str(value.as_double()))
        return Decimal(str(value))

    def _reset_progress_state(self) -> None:
        self._progress_state = "IDLE"
        self._progress_entry = None
        self._progress_initial_stop = None
        self._progress_stop_order_id = None
        self._progress_requested_trigger = None
        self._progress_position_id = None


__all__ = [
    "PROTECTION_TRIGGER_R",
    "ThreeRProgressProtectionMixin",
    "cost_floor_trigger_price",
    "favorable_progress_r",
]
