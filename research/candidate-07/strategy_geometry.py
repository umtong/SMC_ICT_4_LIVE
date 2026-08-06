"""Submission-geometry corrected NautilusTrader strategy for candidate-07."""
from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide

from geometry import adjust_target_for_submission
from model import Direction, TradePlan
from strategy import Candidate07Strategy as _BaseCandidate07Strategy
from strategy import Candidate07StrategyConfig


class Candidate07Strategy(_BaseCandidate07Strategy):
    """Reapply R bounds when the delayed market order is actually submitted."""

    def _submit_pending(self, bar: Bar) -> None:
        plan = self._pending_plan
        if plan is None or self._instrument is None:
            return
        current_price = Decimal(str(bar.close.as_double()))
        stop = Decimal(str(plan.stop_price))
        structural_target = Decimal(str(plan.target_price))
        try:
            geometry = adjust_target_for_submission(
                kind=plan.kind,
                direction=plan.direction,
                entry_reference=current_price,
                stop=stop,
                structural_target=structural_target,
                maximum_reversal_rr=Decimal(str(self.logic.maximum_target_rr)),
                continuation_rr=Decimal(str(self.logic.continuation_target_rr)),
            )
        except ValueError:
            self._invalidate_pending("GAPPED_THROUGH_GEOMETRY", int(bar.ts_event))
            return

        side = OrderSide.BUY if plan.direction is Direction.LONG else OrderSide.SELL
        stop_price = self._instrument.make_price(stop)
        target_price = self._instrument.make_price(geometry.target)
        rounded_stop = stop_price.as_decimal()
        rounded_target = target_price.as_decimal()
        if plan.direction is Direction.LONG:
            risk_distance = current_price - rounded_stop
            reward_distance = rounded_target - current_price
        else:
            risk_distance = rounded_stop - current_price
            reward_distance = current_price - rounded_target
        if risk_distance <= 0 or reward_distance <= 0:
            self._invalidate_pending("GAPPED_THROUGH_ROUNDED_GEOMETRY", int(bar.ts_event))
            return
        actual_rr = reward_distance / risk_distance
        if actual_rr < Decimal(str(self.logic.minimum_rr)):
            self._invalidate_pending("DELAYED_ENTRY_RR_ERODED", int(bar.ts_event))
            return

        equity = Decimal(str(self._current_nav()))
        planned_budget = equity * self.config.risk_fraction
        tick = self._instrument.price_increment.as_decimal()
        entry_fill = current_price + tick if plan.direction is Direction.LONG else current_price - tick
        stop_fill = rounded_stop - tick if plan.direction is Direction.LONG else rounded_stop + tick
        fee_rate = self._instrument.taker_fee or Decimal(0)
        funding_reserve = entry_fill * self.config.risk_funding_reserve_bps / Decimal(10_000)
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
            expected_rr=float(actual_rr),
            details={
                **dict(plan.details),
                "structural_target_before_submission": float(structural_target),
                "target_was_reapplied_at_submission": True,
                "target_was_clamped": geometry.target_was_clamped,
                "submission_reference_price": float(current_price),
                "planned_loss_budget": float(planned_budget),
                "per_unit_expected_loss": float(per_unit_loss),
                "quantity": str(quantity),
                "fee_rate": str(fee_rate),
                "funding_reserve_bps": str(self.config.risk_funding_reserve_bps),
                "slippage_ticks_each_adverse_fill": 1,
            },
        )
        self._active_entry_nav = float(equity)
        self._pending_plan = None
        self._pending_created_ns = None
        self.submit_order_list(order_list)


__all__ = ["Candidate07Strategy", "Candidate07StrategyConfig"]
