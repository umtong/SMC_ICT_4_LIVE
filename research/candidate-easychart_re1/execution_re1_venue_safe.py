"""Venue-safe reduce-only stop replacement for Binance USD-M.

Binance/Nautilus permits price and quantity modification for LIMIT orders but
not in-place trigger modification of futures conditional STOP_MARKET orders.
The research backtest previously relied on that simulation convenience.  This
module uses the same explicit lifecycle in backtest, Demo and production:

1. keep the accepted old reduce-only stop live;
2. submit a new full-quantity reduce-only stop at the tighter trigger;
3. wait for venue acceptance;
4. promote the new stop, then cancel the old sibling;
5. tolerate a temporary pair of reduce-only stops if cancellation is delayed;
6. if a quantity increase cannot obtain full protection, flatten fail-closed.

A rejected tighter stop is not itself an emergency while the old stop still
protects the complete position.  No alpha decision, target, risk fraction, or
management geometry is changed by this transport policy.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from domain import Side
from execution_re1_invalidation import EasyChartRE1InvalidationDecisionStrategy
from execution_re1_management import EasyChartRE1DecisionSwingStrategy, EasyChartRE1StaticStrategy
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy
from nautilus_trader.model.enums import OrderSide, TimeInForce, TriggerType
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCancelRejected,
    OrderCanceled,
    OrderDenied,
    OrderExpired,
    OrderFilled,
    OrderRejected,
)
from nautilus_trader.model.identifiers import ClientOrderId


VENUE_SAFE_STOP_REPLACEMENT_POLICY = (
    "EXTERNAL_METHOD:"
    "KEEP_OLD_REDUCE_ONLY_STOP_UNTIL_NEW_FULL_QUANTITY_STOP_IS_VENUE_ACCEPTED_THEN_CANCEL_OLD"
)


class VenueSafeStopReplacementMixin:
    """Intercept stop modifications and express them as accepted replacements."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.pending_stop_id: ClientOrderId | None = None
        self.pending_stop_trigger: Decimal | None = None
        self.pending_stop_quantity: Decimal | None = None
        self.pending_stop_required_for_quantity = False
        self.retiring_stop_ids: set[ClientOrderId] = set()
        self.all_stop_ids: set[ClientOrderId] = set()

    def _reset_trade_state(self) -> None:
        super()._reset_trade_state()
        self.pending_stop_id = None
        self.pending_stop_trigger = None
        self.pending_stop_quantity = None
        self.pending_stop_required_for_quantity = False
        self.retiring_stop_ids.clear()
        self.all_stop_ids.clear()

    def _protective_ids(self) -> tuple[ClientOrderId, ...]:
        values = list(super()._protective_ids())
        values.extend(self.all_stop_ids)
        if self.pending_stop_id is not None:
            values.append(self.pending_stop_id)
        values.extend(self.retiring_stop_ids)
        output: list[ClientOrderId] = []
        seen: set[ClientOrderId] = set()
        for value in values:
            if value not in seen:
                seen.add(value)
                output.append(value)
        return tuple(output)

    @staticmethod
    def _client_order_id(value: Any) -> ClientOrderId:
        return getattr(value, "client_order_id", value)

    def _current_stop_trigger(self) -> Decimal:
        active = getattr(self, "_active_stop_trigger", None)
        if active is not None:
            return Decimal(str(active))
        if self.active_stop_id is not None:
            order = self.cache.order(self.active_stop_id)
            if order is not None and order.trigger_price is not None:
                return Decimal(str(order.trigger_price))
        if self.active_plan is None:
            raise RuntimeError("stop replacement requested without active plan")
        return Decimal(str(self.active_plan.stop))

    def _start_stop_replacement(
        self,
        *,
        trigger: Decimal,
        quantity: Any,
        required_for_quantity: bool,
        reason: str,
    ) -> None:
        if self.active_plan is None or self.active_instrument_id is None or self.active_stop_id is None:
            raise RuntimeError("stop replacement requested without an active protected position")
        desired = Decimal(str(quantity))
        if desired <= 0:
            raise ValueError("replacement stop quantity must be positive")
        if self.pending_stop_id is not None:
            pending_qty = self.pending_stop_quantity or Decimal("0")
            if desired > pending_qty:
                self._record(
                    "stop_replacement_underprotected_pending_quantity",
                    plan_id=self.active_plan.plan_id,
                    desired_quantity=str(desired),
                    pending_quantity=str(pending_qty),
                    reason=reason,
                    execution_policy=VENUE_SAFE_STOP_REPLACEMENT_POLICY,
                )
                self._request_emergency_flatten("pending_stop_replacement_underprotects_new_quantity")
            return

        instrument = self.instruments[self.active_instrument_id]
        exit_side = OrderSide.SELL if self.active_plan.side is Side.LONG else OrderSide.BUY
        replacement = self.order_factory.stop_market(
            instrument_id=self.active_instrument_id,
            order_side=exit_side,
            quantity=instrument.make_qty(desired),
            trigger_price=instrument.make_price(trigger),
            trigger_type=TriggerType.DEFAULT,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            tags=[
                f"PLAN:{self.active_plan.plan_id}",
                "ROLE:STOP_LOSS",
                "TRANSPORT:REPLACEMENT",
            ],
        )
        self.pending_stop_id = replacement.client_order_id
        self.pending_stop_trigger = trigger
        self.pending_stop_quantity = desired
        self.pending_stop_required_for_quantity = required_for_quantity
        self.all_stop_ids.add(replacement.client_order_id)
        self.submit_order(replacement)
        self._record(
            "stop_replacement_submitted",
            plan_id=self.active_plan.plan_id,
            instrument_id=str(self.active_instrument_id),
            old_stop_client_order_id=str(self.active_stop_id),
            new_stop_client_order_id=str(self.pending_stop_id),
            trigger_price=float(trigger),
            quantity=str(desired),
            required_for_quantity=required_for_quantity,
            reason=reason,
            execution_policy=VENUE_SAFE_STOP_REPLACEMENT_POLICY,
        )

    def modify_order(
        self,
        client_order_id: Any,
        quantity: Any | None = None,
        price: Any | None = None,
        trigger_price: Any | None = None,
        client_id: Any | None = None,
        params: dict | None = None,
    ) -> None:
        cid = self._client_order_id(client_order_id)
        if cid == self.active_stop_id and (quantity is not None or trigger_price is not None):
            order = self.cache.order(cid)
            if order is None or order.is_closed:
                raise RuntimeError("active stop disappeared before replacement")
            desired_quantity = order.quantity if quantity is None else quantity
            desired_trigger = self._current_stop_trigger() if trigger_price is None else Decimal(str(trigger_price))
            old_leaves = Decimal(str(order.leaves_qty))
            desired_total = Decimal(str(desired_quantity))
            self._start_stop_replacement(
                trigger=desired_trigger,
                quantity=desired_total,
                required_for_quantity=desired_total > old_leaves,
                reason="TRIGGER" if trigger_price is not None else "QUANTITY",
            )
            return
        super().modify_order(
            cid,
            quantity=quantity,
            price=price,
            trigger_price=trigger_price,
            client_id=client_id,
            params=params,
        )

    def _sync_protective_quantities(self, open_quantity: Any) -> None:
        if (
            self.active_plan is None
            or self.active_instrument_id is None
            or not self.protection_submitted
            or self.emergency_exit_requested
        ):
            return
        desired_leaves = Decimal(str(open_quantity))
        if desired_leaves <= 0:
            return
        instrument = self.instruments[self.active_instrument_id]
        step = Decimal(str(instrument.size_increment))

        if self.active_stop_id is not None:
            stop = self.cache.order(self.active_stop_id)
            if stop is not None and not stop.is_closed:
                leaves = Decimal(str(stop.leaves_qty))
                if abs(leaves - desired_leaves) >= step:
                    self._start_stop_replacement(
                        trigger=self._current_stop_trigger(),
                        quantity=desired_leaves,
                        required_for_quantity=desired_leaves > leaves,
                        reason="POSITION_QUANTITY_SYNC",
                    )

        if self.active_target_id is not None:
            target = self.cache.order(self.active_target_id)
            if target is not None and not target.is_closed:
                leaves = Decimal(str(target.leaves_qty))
                if abs(leaves - desired_leaves) >= step:
                    filled = Decimal(str(target.filled_qty))
                    desired_total = filled + desired_leaves
                    try:
                        super().modify_order(
                            self.active_target_id,
                            quantity=instrument.make_qty(desired_total),
                        )
                        self._record(
                            "target_quantity_sync_requested",
                            plan_id=self.active_plan.plan_id,
                            client_order_id=str(self.active_target_id),
                            desired_leaves=str(desired_leaves),
                            desired_total=str(desired_total),
                        )
                    except Exception as exc:
                        self._record(
                            "target_quantity_sync_exception",
                            plan_id=self.active_plan.plan_id,
                            reason=repr(exc),
                        )
                        self._request_emergency_flatten("target_quantity_sync_exception")

    def _clear_failed_pending_stop(self, event: Any, reason: str) -> bool:
        if self.pending_stop_id is None or event.client_order_id != self.pending_stop_id:
            return False
        failed_id = self.pending_stop_id
        required = self.pending_stop_required_for_quantity
        self.all_stop_ids.discard(failed_id)
        self.pending_stop_id = None
        self.pending_stop_trigger = None
        self.pending_stop_quantity = None
        self.pending_stop_required_for_quantity = False
        if hasattr(self, "_pending_trail_stop"):
            self._pending_trail_stop = None
        self._record(
            "stop_replacement_failed_old_stop_retained",
            plan_id=None if self.active_plan is None else self.active_plan.plan_id,
            client_order_id=str(failed_id),
            reason=reason,
            required_for_quantity=required,
            execution_policy=VENUE_SAFE_STOP_REPLACEMENT_POLICY,
        )
        if required:
            self._request_emergency_flatten("required_quantity_stop_replacement_failed")
        return True

    def on_order_accepted(self, event: OrderAccepted) -> None:
        super().on_order_accepted(event)
        if self.pending_stop_id is None or event.client_order_id != self.pending_stop_id:
            if event.client_order_id == self.active_stop_id:
                self.all_stop_ids.add(event.client_order_id)
            return
        new_id = self.pending_stop_id
        old_id = self.active_stop_id
        trigger = self.pending_stop_trigger
        self.pending_stop_id = None
        self.pending_stop_trigger = None
        self.pending_stop_quantity = None
        self.pending_stop_required_for_quantity = False
        self.active_stop_id = new_id
        if trigger is not None and hasattr(self, "_active_stop_trigger"):
            self._active_stop_trigger = trigger
        if hasattr(self, "_pending_trail_stop"):
            self._pending_trail_stop = None
        if old_id is not None and old_id != new_id:
            self.retiring_stop_ids.add(old_id)
            self.expected_cancel_ids.add(old_id)
            old_order = self.cache.order(old_id)
            if old_order is not None and not old_order.is_closed:
                try:
                    self.cancel_order(old_id)
                except Exception as exc:
                    self._record(
                        "retiring_stop_cancel_exception_both_reduce_only_retained",
                        plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                        old_stop_client_order_id=str(old_id),
                        new_stop_client_order_id=str(new_id),
                        reason=repr(exc),
                        execution_policy=VENUE_SAFE_STOP_REPLACEMENT_POLICY,
                    )
        self._record(
            "stop_replacement_accepted",
            plan_id=None if self.active_plan is None else self.active_plan.plan_id,
            old_stop_client_order_id=None if old_id is None else str(old_id),
            new_stop_client_order_id=str(new_id),
            trigger_price=None if trigger is None else float(trigger),
            execution_policy=VENUE_SAFE_STOP_REPLACEMENT_POLICY,
        )

    def on_order_rejected(self, event: OrderRejected) -> None:
        if self._clear_failed_pending_stop(event, f"rejected:{event.reason}"):
            return
        super().on_order_rejected(event)

    def on_order_denied(self, event: OrderDenied) -> None:
        if self._clear_failed_pending_stop(event, f"denied:{event.reason}"):
            return
        super().on_order_denied(event)

    def on_order_expired(self, event: OrderExpired) -> None:
        if self._clear_failed_pending_stop(event, "expired"):
            return
        super().on_order_expired(event)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        if self._clear_failed_pending_stop(event, "canceled_before_acceptance"):
            return
        self.retiring_stop_ids.discard(event.client_order_id)
        self.all_stop_ids.discard(event.client_order_id)
        super().on_order_canceled(event)

    def on_order_cancel_rejected(self, event: OrderCancelRejected) -> None:
        if event.client_order_id in self.retiring_stop_ids:
            self._record(
                "retiring_stop_cancel_rejected_both_reduce_only_retained",
                plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                client_order_id=str(event.client_order_id),
                reason=str(event.reason),
                execution_policy=VENUE_SAFE_STOP_REPLACEMENT_POLICY,
            )
            return
        super().on_order_cancel_rejected(event)

    def on_order_filled(self, event: OrderFilled) -> None:
        plan_id = self.active_plan.plan_id if self.active_plan else None
        structural_exit_id = getattr(self, "structural_exit_id", None)
        if event.client_order_id == self.active_entry_id:
            role = "ENTRY"
        elif event.client_order_id in self.all_stop_ids or event.client_order_id in self.retiring_stop_ids:
            role = "STOP_LOSS"
        elif event.client_order_id == self.active_target_id:
            role = "TAKE_PROFIT"
        elif event.client_order_id == structural_exit_id:
            role = "STRUCTURAL_INVALIDATION"
        else:
            role = "OTHER_OR_EMERGENCY_EXIT"
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


class EasyChartRE1VenueSafeStaticStrategy(VenueSafeStopReplacementMixin, EasyChartRE1StaticStrategy):
    pass


class EasyChartRE1VenueSafeStructuralStrategy(
    VenueSafeStopReplacementMixin,
    EasyChartRE1StructuralFixedStrategy,
):
    pass


class EasyChartRE1VenueSafeDecisionStrategy(
    VenueSafeStopReplacementMixin,
    EasyChartRE1DecisionSwingStrategy,
):
    pass


class EasyChartRE1VenueSafeInvalidationStrategy(
    VenueSafeStopReplacementMixin,
    EasyChartRE1InvalidationDecisionStrategy,
):
    pass


__all__ = [
    "EasyChartRE1VenueSafeStaticStrategy",
    "EasyChartRE1VenueSafeStructuralStrategy",
    "EasyChartRE1VenueSafeDecisionStrategy",
    "EasyChartRE1VenueSafeInvalidationStrategy",
    "VENUE_SAFE_STOP_REPLACEMENT_POLICY",
]
