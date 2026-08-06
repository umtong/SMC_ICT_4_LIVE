"""NautilusTrader execution for the combined causal auction router."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import PositionClosed

from auction_continuation import (
    CONTINUATION_TARGET_RR,
    CombinedAuctionRouter,
)
from model import Direction, ScenarioKind, TradePlan
from strategy_flow import Candidate07FlowStrategy, Candidate07FlowStrategyConfig


class Candidate07AuctionStrategy(Candidate07FlowStrategy):
    """Execute frozen absorption reversal plus two accepted-auction branches."""

    def __init__(self, config: Candidate07FlowStrategyConfig):
        super().__init__(config)
        failed_timeout_bars = max(
            1,
            int(config.max_hold_minutes) // int(self.logic.signal_minutes),
        )
        self.router = CombinedAuctionRouter(
            self.logic,
            failed_timeout_bars=failed_timeout_bars,
        )

    def _submit_pending(self, bar: Bar) -> None:
        plan = self._pending_plan
        if plan is None:
            return
        if plan.kind is ScenarioKind.ACCEPTANCE_CONTINUATION:
            self._submit_continuation(bar, plan)
            return
        super()._submit_pending(bar)

    def _submit_continuation(self, bar: Bar, plan: TradePlan) -> None:
        if self._instrument is None:
            self._invalidate_pending("INSTRUMENT_NOT_READY", int(bar.ts_event))
            return
        current_price = Decimal(str(bar.close.as_double()))
        stop = Decimal(str(plan.stop_price))
        if plan.direction is Direction.LONG:
            risk_distance = current_price - stop
            side = OrderSide.BUY
        else:
            risk_distance = stop - current_price
            side = OrderSide.SELL
        if risk_distance <= 0:
            self._invalidate_pending(
                "CONTINUATION_GAPPED_THROUGH_STRUCTURAL_STOP",
                int(bar.ts_event),
            )
            return

        # The measured leg is re-anchored to the first executable one-minute
        # close. No stale signal-bar target or future price is used.
        target = (
            current_price + risk_distance * Decimal(str(CONTINUATION_TARGET_RR))
            if plan.direction is Direction.LONG
            else current_price - risk_distance * Decimal(str(CONTINUATION_TARGET_RR))
        )
        equity = Decimal(str(self._current_nav()))
        planned_budget = equity * self.config.risk_fraction
        tick = self._instrument.price_increment.as_decimal()
        entry_fill = (
            current_price + tick
            if plan.direction is Direction.LONG
            else current_price - tick
        )
        stop_fill = (
            stop - tick
            if plan.direction is Direction.LONG
            else stop + tick
        )
        fee_rate = self._instrument.taker_fee or Decimal(0)
        funding_reserve = (
            entry_fill
            * self.config.risk_funding_reserve_bps
            / Decimal(10_000)
        )
        per_unit_loss = (
            abs(entry_fill - stop_fill)
            + entry_fill * fee_rate
            + stop_fill * fee_rate
            + funding_reserve
        )
        if per_unit_loss <= 0:
            self._invalidate_pending("NONPOSITIVE_UNIT_LOSS", int(bar.ts_event))
            return
        raw_qty = planned_budget / per_unit_loss
        quantity = self._instrument.make_qty(raw_qty)
        if quantity.as_decimal() <= 0:
            self._invalidate_pending("QUANTITY_ROUNDED_TO_ZERO", int(bar.ts_event))
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
        self._active_plan = TradePlan(
            scenario_id=plan.scenario_id,
            kind=plan.kind,
            direction=plan.direction,
            observed_time_ns=plan.observed_time_ns,
            entry_reference=float(current_price),
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
                "slippage_ticks_each_adverse_fill": 1,
                "target_reanchored_at_actual_entry": True,
            },
        )
        self._active_entry_nav = float(equity)
        self._pending_plan = None
        self._pending_created_ns = None
        self.submit_order_list(order_list)

    def _invalidate_pending(self, reason: str, event_time_ns: int) -> None:
        plan = self._pending_plan
        if (
            plan is not None
            and plan.kind is ScenarioKind.ABSORPTION_RECLAIM
            and reason
            in {
                "FLOW_DELAYED_ENTRY_STOP_OUTSIDE_STATE",
                "FLOW_DELAYED_ENTRY_GEOMETRY_INVALID",
                "DELAYED_ENTRY_RR_ERODED",
                "GAPPED_THROUGH_GEOMETRY",
            }
        ):
            self.router.arm_failed_from_plan(
                plan,
                event_time_ns=event_time_ns,
                boundary_touched=False,
            )
            self._append_manual_event(
                scenario_id=f"{plan.scenario_id}-fac",
                previous_state="IDLE",
                next_state="FAILED_ACCEPTANCE_ARMED",
                reason_code="MISSED_ABSORPTION_MONITORED_FOR_ACCEPTANCE",
                event_time_ns=event_time_ns,
                reference_price=plan.stop_price,
                details={
                    "source_scenario_id": plan.scenario_id,
                    "source_reason": reason,
                },
            )
        super()._invalidate_pending(reason, event_time_ns)

    def on_position_closed(self, event: PositionClosed) -> None:
        plan = self._active_plan
        structural_stop = (
            plan is not None
            and plan.kind is ScenarioKind.ABSORPTION_RECLAIM
            and self._is_structural_stop(event, plan)
        )
        super().on_position_closed(event)
        if plan is None or not structural_stop:
            return
        self.router.arm_failed_from_plan(
            plan,
            event_time_ns=int(event.ts_event),
            boundary_touched=True,
        )
        self._append_manual_event(
            scenario_id=f"{plan.scenario_id}-fac",
            previous_state="IDLE",
            next_state="FAILED_BOUNDARY_TOUCHED",
            reason_code="ACTUAL_ABSORPTION_STOP_MONITORED_FOR_ACCEPTANCE",
            event_time_ns=int(event.ts_event),
            reference_price=plan.stop_price,
            details={"source_scenario_id": plan.scenario_id},
        )

    def _is_structural_stop(
        self,
        event: PositionClosed,
        plan: TradePlan,
    ) -> bool:
        closing_order_id = getattr(event, "closing_order_id", None)
        order = (
            self.cache.order(closing_order_id)
            if closing_order_id is not None
            else None
        )
        if order is not None:
            tags = {
                str(tag).upper()
                for tag in (getattr(order, "tags", None) or ())
            }
            if "STOP_LOSS" in tags:
                return True
            if "TAKE_PROFIT" in tags:
                return False
            if "STOP_MARKET" in str(
                getattr(order, "order_type", "")
            ).upper():
                return True

        raw_last_px: Any = getattr(event, "last_px", None)
        if raw_last_px is None:
            return False
        last_px = (
            raw_last_px.as_double()
            if hasattr(raw_last_px, "as_double")
            else float(raw_last_px)
        )
        tolerance = (
            2.0 * self._instrument.price_increment.as_double()
            if self._instrument is not None
            else max(abs(plan.stop_price) * 1e-9, 1e-9)
        )
        return abs(last_px - plan.stop_price) <= tolerance


__all__ = ["Candidate07AuctionStrategy", "Candidate07FlowStrategyConfig"]
