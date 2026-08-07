"""Market-if-touched take-profit execution for transient auction targets.

This class changes no signal, entry, stop, quantity, risk budget, target price,
position slot or holding horizon from the serialized baseline.  It changes only
the take-profit child from a probabilistic passive LIMIT to a taker
MARKET_IF_TOUCHED order, because the pre-attack value is a transient delivery
checkpoint whose economic validity depends on closing when touched.
"""
from __future__ import annotations

from decimal import Decimal
import json

from nautilus_trader.model.enums import OrderSide, OrderType

from event_signal_data import CausalTradeSignal
from strategy_event_signal_safe import Candidate07SerializedEventStrategy


class Candidate07MITSerializedStrategy(Candidate07SerializedEventStrategy):
    """Serialized execution with a market-if-touched take-profit child."""

    def _submit_signal(self, signal: CausalTradeSignal, bar: object) -> None:
        if self._instrument is None:
            raise RuntimeError("instrument is not initialized")
        current_price = Decimal(str(bar.close.as_double()))
        stop = Decimal(str(signal.stop_price))
        target = Decimal(str(signal.target_price))
        if signal.direction == "LONG":
            risk_distance = current_price - stop
            reward_distance = target - current_price
            side = OrderSide.BUY
        else:
            risk_distance = stop - current_price
            reward_distance = current_price - target
            side = OrderSide.SELL
        if risk_distance <= 0 or reward_distance <= 0:
            self._diagnostics.append(
                {
                    "scenario_id": signal.scenario_id,
                    "reason": "SIGNAL_GEOMETRY_ALREADY_INVALID",
                    "current_price": float(current_price),
                    "stop": float(stop),
                    "target": float(target),
                }
            )
            return
        actual_rr = reward_distance / risk_distance
        if actual_rr < self.config.minimum_rr:
            self._diagnostics.append(
                {
                    "scenario_id": signal.scenario_id,
                    "reason": "SIGNAL_RR_ERODED_BEFORE_SUBMISSION",
                    "actual_rr": float(actual_rr),
                    "minimum_rr": float(self.config.minimum_rr),
                }
            )
            return

        equity = Decimal(str(self._current_nav()))
        planned_budget = equity * self.config.risk_fraction
        tick = self._instrument.price_increment.as_decimal()
        expected_entry_fill = (
            current_price + tick
            if signal.direction == "LONG"
            else current_price - tick
        )
        expected_stop_fill = (
            stop - tick if signal.direction == "LONG" else stop + tick
        )
        fee_rate = self._instrument.taker_fee or Decimal(0)
        funding_reserve = (
            expected_entry_fill
            * self.config.risk_funding_reserve_bps
            / Decimal(10_000)
        )
        per_unit_loss = (
            abs(expected_entry_fill - expected_stop_fill)
            + expected_entry_fill * fee_rate
            + expected_stop_fill * fee_rate
            + funding_reserve
        )
        if per_unit_loss <= 0:
            self._diagnostics.append(
                {
                    "scenario_id": signal.scenario_id,
                    "reason": "NONPOSITIVE_UNIT_LOSS",
                }
            )
            return
        quantity = self._instrument.make_qty(planned_budget / per_unit_loss)
        if quantity.as_decimal() <= 0:
            self._diagnostics.append(
                {
                    "scenario_id": signal.scenario_id,
                    "reason": "QUANTITY_ROUNDED_TO_ZERO",
                }
            )
            return

        stop_price = self._instrument.make_price(stop)
        target_price = self._instrument.make_price(target)
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            tp_order_type=OrderType.MARKET_IF_TOUCHED,
            tp_trigger_price=target_price,
            tp_post_only=False,
            sl_trigger_price=stop_price,
        )
        self._active_signal = CausalTradeSignal(
            instrument_id=signal.instrument_id,
            scenario_id=signal.scenario_id,
            direction=signal.direction,
            entry_reference=float(current_price),
            stop_price=stop_price.as_double(),
            target_price=target_price.as_double(),
            expected_rr=float(actual_rr),
            source_pool_id=signal.source_pool_id,
            signal_kind=f"{signal.signal_kind}_MIT_TP",
            details_json=json.dumps(
                {
                    **json.loads(signal.details_json),
                    "planned_loss_budget": float(planned_budget),
                    "per_unit_expected_loss": float(per_unit_loss),
                    "quantity": str(quantity),
                    "fee_rate": str(fee_rate),
                    "funding_reserve_bps": str(
                        self.config.risk_funding_reserve_bps
                    ),
                    "slippage_ticks_each_adverse_fill": 1,
                    "signal_delivery_ns": signal.ts_event,
                    "take_profit_order_type": "MARKET_IF_TOUCHED",
                    "take_profit_liquidity_role": "TAKER",
                },
                sort_keys=True,
            ),
            observed_time_ns=signal.observed_time_ns,
            ts_event=signal.ts_event,
            ts_init=signal.ts_init,
        )
        self._active_entry_nav = float(equity)
        self._append_event(
            scenario_id=signal.scenario_id,
            previous_state="ENTRY_READY",
            next_state="ORDER_SUBMITTED",
            reason_code="NAUTILUS_MIT_BRACKET_SUBMITTED",
            event_time_ns=int(signal.ts_event),
            reference_price=float(current_price),
            details={
                "quantity": str(quantity),
                "planned_loss_budget": float(planned_budget),
                "actual_rr": float(actual_rr),
                "take_profit_order_type": "MARKET_IF_TOUCHED",
            },
        )
        self.submit_order_list(order_list)


__all__ = ["Candidate07MITSerializedStrategy"]
