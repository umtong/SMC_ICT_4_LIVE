"""Controlled LAST_PRICE stop emulation for candidate 10.

The official-L1 control proved that a native ``STOP_MARKET`` can be rejected by
Nautilus' venue-side bid/ask validation even when the actual last trade has not
crossed the structural stop. This subclass changes only order routing: the
unchanged stop price is held by Nautilus' ``OrderEmulator`` and released as a
reduce-only market order when an actual TradeTick reaches it.
"""

from __future__ import annotations

from typing import Any

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TriggerType

from c10_flow_evidence_fix import EvidenceValidatedParentProtectedStrategy
from c10_flow_parent_execution import protective_action


PROTECTIVE_STOP_TRIGGER_TYPE = TriggerType.LAST_PRICE
PROTECTIVE_STOP_EMULATION_TRIGGER_TYPE = TriggerType.LAST_PRICE


class EmulatedStopEvidenceValidatedStrategy(
    EvidenceValidatedParentProtectedStrategy,
):
    """Protect each parent fill with a locally emulated LAST_PRICE stop."""

    def _submit_protection_for_fill(self, event: Any) -> None:
        if self.active_trade is None:
            return
        quantity = event.last_qty
        direction = int(self.active_trade["direction"])
        stop_value = float(self.active_trade["stop"])
        target_value = float(self.active_trade["target"])
        last_price = (
            self.last_trade_price
            if self.last_trade_price is not None
            else event.last_px.as_double()
        )
        exit_side = OrderSide.SELL if direction > 0 else OrderSide.BUY
        action = protective_action(
            direction=direction,
            last_price=last_price,
            stop_price=stop_value,
            target_price=target_value,
        )
        ts_ns = int(getattr(event, "ts_event", self.clock.timestamp_ns()))
        if action == "MARKET_STOP":
            self._submit_market_exit(
                quantity=quantity,
                side=exit_side,
                role="PROTECTIVE_STOP_GAP_MARKET",
                ts_ns=ts_ns,
                last_price=last_price,
            )
            return
        if action == "MARKET_TARGET":
            self._submit_market_exit(
                quantity=quantity,
                side=exit_side,
                role="PROTECTIVE_TARGET_GAP_MARKET",
                ts_ns=ts_ns,
                last_price=last_price,
            )
            return

        assert self.instrument is not None
        stop = self.instrument.make_price(stop_value)
        target = self.instrument.make_price(target_value)
        stop_order = self.order_factory.stop_market(
            instrument_id=self.config.instrument_id,
            order_side=exit_side,
            quantity=quantity,
            trigger_price=stop,
            trigger_type=PROTECTIVE_STOP_TRIGGER_TYPE,
            reduce_only=True,
            emulation_trigger=PROTECTIVE_STOP_EMULATION_TRIGGER_TYPE,
            tags=["PROTECTIVE_STOP_EMULATED_LAST_PRICE"],
        )
        target_order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=exit_side,
            quantity=quantity,
            price=target,
            post_only=True,
            reduce_only=True,
            tags=["PROTECTIVE_TARGET_POST_ONLY"],
        )
        self._register_exit(stop_order, "PROTECTIVE_STOP")
        self._register_exit(target_order, "PROTECTIVE_TARGET")
        self._register_pair(stop_order, target_order)
        self.protected_chunk_count += 1
        self._append_execution_event(
            event_type="PROTECTION_SUBMITTED",
            reason_code=(
                "PER_FILL_REDUCE_ONLY_EMULATED_LAST_PRICE_STOP_TARGET_SUBMITTED"
            ),
            ts_ns=ts_ns,
            previous_state="PARENT_PARTIAL_FILL",
            next_state="POSITION_PROTECTED",
            reference_price=last_price,
            details={
                "quantity": quantity.as_double(),
                "stop": stop_value,
                "target": target_value,
                "stop_trigger_type": str(PROTECTIVE_STOP_TRIGGER_TYPE),
                "stop_emulation_trigger": str(
                    PROTECTIVE_STOP_EMULATION_TRIGGER_TYPE,
                ),
                "stop_client_order_id": str(stop_order.client_order_id),
                "target_client_order_id": str(target_order.client_order_id),
                "protected_chunk_count": self.protected_chunk_count,
            },
        )
        self.submit_order(stop_order)
        if not self.emergency_flatten_pending:
            self.submit_order(target_order)


__all__ = [
    "EmulatedStopEvidenceValidatedStrategy",
    "PROTECTIVE_STOP_EMULATION_TRIGGER_TYPE",
    "PROTECTIVE_STOP_TRIGGER_TYPE",
]
