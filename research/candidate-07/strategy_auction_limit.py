"""Limit-parent execution for causally confirmed continuation scenarios."""
from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
from nautilus_trader.model.events import OrderCanceled, OrderRejected, PositionClosed, PositionOpened

from auction_continuation import CONTINUATION_TARGET_RR
from model import Direction, ScenarioKind, ScenarioState, TradePlan
from strategy_auction import Candidate07AuctionStrategy
from strategy_flow import Candidate07FlowStrategyConfig, NS_PER_MINUTE


class Candidate07AuctionLimitStrategy(Candidate07AuctionStrategy):
    """Do not chase a completed mitigation; rest at its observed close."""

    def __init__(self, config: Candidate07FlowStrategyConfig):
        super().__init__(config)
        self._continuation_entry_pending = False
        self._continuation_entry_deadline_ns: int | None = None
        self._continuation_cancel_requested = False
        self._continuation_cancel_reason: str | None = None

    def on_bar(self, bar: Bar) -> None:
        self._manage_continuation_entry(bar)
        super().on_bar(bar)

    def _submit_continuation(self, bar: Bar, plan: TradePlan) -> None:
        if self._instrument is None:
            self._invalidate_pending("INSTRUMENT_NOT_READY", int(bar.ts_event))
            return
        current_price = Decimal(str(bar.close.as_double()))
        entry = Decimal(str(plan.entry_reference))
        stop = Decimal(str(plan.stop_price))
        if plan.direction is Direction.LONG:
            risk_distance = entry - stop
            side = OrderSide.BUY
            market_through_stop = current_price <= stop
        else:
            risk_distance = stop - entry
            side = OrderSide.SELL
            market_through_stop = current_price >= stop
        if risk_distance <= 0 or market_through_stop:
            self._invalidate_pending(
                "CONTINUATION_LIMIT_GEOMETRY_INVALID",
                int(bar.ts_event),
            )
            return

        # The completed mitigation bar defines the maximum acceptable entry.
        # The limit can fill at that price or better and therefore cannot turn
        # a confirmed pullback into a later market-order chase.
        target = (
            entry + risk_distance * Decimal(str(CONTINUATION_TARGET_RR))
            if plan.direction is Direction.LONG
            else entry - risk_distance * Decimal(str(CONTINUATION_TARGET_RR))
        )
        equity = Decimal(str(self._current_nav()))
        planned_budget = equity * self.config.risk_fraction
        tick = self._instrument.price_increment.as_decimal()
        stop_fill = (
            stop - tick
            if plan.direction is Direction.LONG
            else stop + tick
        )
        fee_rate = self._instrument.taker_fee or Decimal(0)
        funding_reserve = (
            entry
            * self.config.risk_funding_reserve_bps
            / Decimal(10_000)
        )
        # Use taker fees for the parent even when the resting limit later earns
        # maker status, so planned loss cannot exceed the 3% budget from a fee
        # assumption that is favorable but not guaranteed.
        per_unit_loss = (
            abs(entry - stop_fill)
            + entry * fee_rate
            + stop_fill * fee_rate
            + funding_reserve
        )
        if per_unit_loss <= 0:
            self._invalidate_pending("NONPOSITIVE_UNIT_LOSS", int(bar.ts_event))
            return
        quantity = self._instrument.make_qty(planned_budget / per_unit_loss)
        if quantity.as_decimal() <= 0:
            self._invalidate_pending("QUANTITY_ROUNDED_TO_ZERO", int(bar.ts_event))
            return

        entry_price = self._instrument.make_price(entry)
        stop_price = self._instrument.make_price(stop)
        target_price = self._instrument.make_price(target)
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            entry_order_type=OrderType.LIMIT,
            entry_price=entry_price,
            time_in_force=TimeInForce.GTC,
            entry_post_only=False,
            sl_trigger_price=stop_price,
            tp_price=target_price,
        )
        horizon_minutes = (
            int(self.logic.confirmation_bars)
            * int(self.logic.signal_minutes)
        )
        self._active_plan = TradePlan(
            scenario_id=plan.scenario_id,
            kind=plan.kind,
            direction=plan.direction,
            observed_time_ns=plan.observed_time_ns,
            entry_reference=entry_price.as_double(),
            stop_price=stop_price.as_double(),
            target_price=target_price.as_double(),
            liquidity_level=plan.liquidity_level,
            expected_rr=CONTINUATION_TARGET_RR,
            details={
                **dict(plan.details),
                "planned_loss_budget": float(planned_budget),
                "per_unit_expected_loss": float(per_unit_loss),
                "quantity": str(quantity),
                "fee_rate": str(fee_rate),
                "funding_reserve_bps": str(
                    self.config.risk_funding_reserve_bps
                ),
                "entry_order_type": "LIMIT",
                "entry_limit_price": entry_price.as_double(),
                "entry_limit_horizon_minutes": horizon_minutes,
                "entry_adverse_slippage_ticks": 0,
                "stop_adverse_slippage_ticks": 1,
            },
        )
        self._active_entry_nav = float(equity)
        self._pending_plan = None
        self._pending_created_ns = None
        self._continuation_entry_pending = True
        self._continuation_entry_deadline_ns = (
            int(bar.ts_event) + horizon_minutes * NS_PER_MINUTE
        )
        self._continuation_cancel_requested = False
        self._continuation_cancel_reason = None
        self.submit_order_list(order_list)

    def _manage_continuation_entry(self, bar: Bar) -> None:
        if (
            not self._continuation_entry_pending
            or self._continuation_cancel_requested
            or self._active_plan is None
            or self._active_plan.kind is not ScenarioKind.ACCEPTANCE_CONTINUATION
            or not self.portfolio.is_flat(self.config.instrument_id)
        ):
            return
        now = int(bar.ts_event)
        close = bar.close.as_double()
        plan = self._active_plan
        reclaimed = (
            close <= plan.liquidity_level
            if plan.direction is Direction.LONG
            else close >= plan.liquidity_level
        )
        expired = (
            self._continuation_entry_deadline_ns is not None
            and now >= self._continuation_entry_deadline_ns
        )
        if not reclaimed and not expired:
            return
        self._continuation_cancel_requested = True
        self._continuation_cancel_reason = (
            "LIMIT_ENTRY_POOL_RECLAIMED"
            if reclaimed
            else "LIMIT_ENTRY_CONFIRMATION_HORIZON_EXPIRED"
        )
        self.cancel_all_orders(self.config.instrument_id)

    def on_position_opened(self, event: PositionOpened) -> None:
        if event.instrument_id == self.config.instrument_id:
            self._clear_limit_entry_state()
        super().on_position_opened(event)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        if (
            event.instrument_id == self.config.instrument_id
            and self._continuation_entry_pending
            and self.portfolio.is_flat(self.config.instrument_id)
            and self._active_plan is not None
            and self._active_plan.kind is ScenarioKind.ACCEPTANCE_CONTINUATION
        ):
            plan = self._active_plan
            self._append_manual_event(
                scenario_id=plan.scenario_id,
                previous_state=ScenarioState.ENTRY_READY.value,
                next_state=ScenarioState.INVALIDATED.value,
                reason_code=(
                    self._continuation_cancel_reason
                    or "CONTINUATION_LIMIT_ENTRY_CANCELED"
                ),
                event_time_ns=int(event.ts_event),
                reference_price=plan.entry_reference,
                details={"client_order_id": str(event.client_order_id)},
            )
            self._active_plan = None
            self._active_entry_nav = None
            self._clear_limit_entry_state()

    def on_order_rejected(self, event: OrderRejected) -> None:
        super().on_order_rejected(event)
        if event.instrument_id == self.config.instrument_id:
            self._clear_limit_entry_state()

    def on_position_closed(self, event: PositionClosed) -> None:
        super().on_position_closed(event)
        if event.instrument_id == self.config.instrument_id:
            self._clear_limit_entry_state()

    def _clear_limit_entry_state(self) -> None:
        self._continuation_entry_pending = False
        self._continuation_entry_deadline_ns = None
        self._continuation_cancel_requested = False
        self._continuation_cancel_reason = None


__all__ = [
    "Candidate07AuctionLimitStrategy",
    "Candidate07FlowStrategyConfig",
]
