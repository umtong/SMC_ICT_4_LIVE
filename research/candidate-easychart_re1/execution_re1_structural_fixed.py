"""Nautilus 1.230 binding fix for RE1 structural stop updates.

The installed Cython strategy surface accepts the cached ``Order`` object for
``modify_order``.  The generated Rust/Python stub advertises a client-order ID,
but the runtime used by this project rejects that object type.  This subclass
changes only that transport detail; the causal swing, cost-secure ratchet and
all trade decisions remain those in ``execution_re1_structural``.
"""
from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.data import Bar

from domain import Side
from execution_re1_structural import (
    EasyChartRE1StructuralStrategy,
    STRUCTURAL_PROFIT_PROTECTION_RULE,
)


STRUCTURAL_MODIFY_TRANSPORT_RULE = (
    "EXTERNAL_METHOD:NAUTILUS_1_230_MODIFY_ORDER_REQUIRES_CACHED_ORDER_OBJECT"
)


class EasyChartRE1StructuralFixedStrategy(EasyChartRE1StructuralStrategy):
    """Identical structural management with the runtime-compatible modify call."""

    def _request_structural_stop(self, bar: Bar) -> None:
        if (
            self.active_plan is None
            or self.active_instrument_id is None
            or self.active_stop_id is None
            or self._position_opened_ts_ns is None
            or self._actual_entry_price is None
            or self.emergency_exit_requested
            or self.position_closed_seen
        ):
            return
        if int(bar.ts_event) <= self._position_opened_ts_ns:
            return
        if self._pending_trail_stop is not None:
            return

        item = self._closed_execution_bar(bar)
        if self._trail_bars and item.ts_event <= self._trail_bars[-1].ts_event:
            raise RuntimeError("structural-management bars must be strictly increasing")
        self._trail_bars.append(item)
        if len(self._trail_bars) > 3:
            del self._trail_bars[:-3]
        if len(self._trail_bars) < 3:
            return

        side = self.active_plan.side
        instrument = self.instruments[self.active_instrument_id]
        tick = Decimal(str(instrument.price_increment))
        swing = self._strict_swing_candidate(side, *self._trail_bars, tick)
        if swing is None:
            return
        candidate, basis = swing

        if side is Side.LONG and not candidate < item.low:
            raise RuntimeError("long structural stop was already traded by its confirmation bar")
        if side is Side.SHORT and not candidate > item.high:
            raise RuntimeError("short structural stop was already traded by its confirmation bar")

        current = self._active_stop_trigger
        if current is None:
            current = Decimal(str(self.active_plan.stop))
        if not self._improves_stop(side, candidate, current, tick):
            return
        locks_profit, net_locked_per_unit = self._locks_positive_net(instrument, side, candidate)
        if not locks_profit:
            return

        stop_order = self.cache.order(self.active_stop_id)
        if stop_order is None or stop_order.is_closed:
            return
        try:
            self._pending_trail_stop = candidate
            self.modify_order(
                stop_order,
                trigger_price=instrument.make_price(candidate),
            )
            self._record(
                "structural_profit_stop_requested",
                plan_id=self.active_plan.plan_id,
                instrument_id=str(self.active_instrument_id),
                stop_client_order_id=str(self.active_stop_id),
                previous_stop=float(current),
                requested_stop=float(candidate),
                actual_entry=float(self._actual_entry_price),
                net_locked_per_unit_after_reserve=float(net_locked_per_unit),
                pivot_time_ns=self._trail_bars[1].ts_event,
                confirmation_time_ns=item.ts_event,
                basis=basis,
                modify_transport=STRUCTURAL_MODIFY_TRANSPORT_RULE,
                rule_provenance=STRUCTURAL_PROFIT_PROTECTION_RULE,
            )
        except Exception as exc:
            self._pending_trail_stop = None
            self._record(
                "structural_profit_stop_exception",
                plan_id=self.active_plan.plan_id,
                instrument_id=str(self.active_instrument_id),
                reason=repr(exc),
                modify_transport=STRUCTURAL_MODIFY_TRANSPORT_RULE,
                rule_provenance=STRUCTURAL_PROFIT_PROTECTION_RULE,
            )
            self._request_emergency_flatten("structural_profit_stop_exception")


__all__ = [
    "EasyChartRE1StructuralFixedStrategy",
    "STRUCTURAL_MODIFY_TRANSPORT_RULE",
]
