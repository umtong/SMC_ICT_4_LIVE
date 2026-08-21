"""Causal full-position management for EasyChart RE1.

The first RE1 diagnostics left every position unchanged until its original stop
or sometimes very distant structural target.  That is unlike the supplied live
examples: once price creates a new favorable micro structure, the trader no
longer allows the entire move to turn into the original loss.  Partial exits are
intentionally still excluded, so the closest machine analogue is one full-size
protective stop ratcheted behind newly confirmed one-minute swings.

Policy
------
* Entry, initial invalidation and structural target remain fixed before entry.
* Only bars completed after the actual position-open event are eligible.
* A strict three-bar swing is causal on the close of its right-hand bar.
* The stop becomes effective only for later bars and is placed one tick beyond
  the confirmed swing extreme.
* It never loosens and it is moved only when the new stop locks a positive net
  result after the configured entry/exit fee, slippage and funding reserves.
* No fixed R multiple, holding timer, daily rule, score or outcome information
  is used.

This keeps the original opportunity for a structural target while replacing the
unnatural "full winner back to -1R" path with an auditable market-structure
response.  The same stop-modification path is used by backtest and demo/live.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from nautilus_trader.model.data import Bar
from nautilus_trader.model.events import OrderModifyRejected, OrderUpdated, PositionOpened
from nautilus_trader.model.identifiers import InstrumentId

from domain import Side
from execution_re1 import EasyChartRE1Strategy


STRUCTURAL_PROFIT_PROTECTION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "AFTER_ENTRY_CONFIRMED_ONE_MINUTE_SWING_RATCHETS_FULL_STOP_ONLY_WHEN_NET_PROFIT_IS_LOCKED"
)


@dataclass(frozen=True, slots=True)
class _ClosedExecutionBar:
    ts_event: int
    high: Decimal
    low: Decimal
    close: Decimal


class EasyChartRE1StructuralStrategy(EasyChartRE1Strategy):
    """RE1 execution with causal, cost-secure one-minute structure protection."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self._trail_bars: list[_ClosedExecutionBar] = []
        self._position_opened_ts_ns: int | None = None
        self._actual_entry_price: Decimal | None = None
        self._active_stop_trigger: Decimal | None = None
        self._pending_trail_stop: Decimal | None = None

    def _clear_structural_management(self) -> None:
        self._trail_bars.clear()
        self._position_opened_ts_ns = None
        self._actual_entry_price = None
        self._active_stop_trigger = None
        self._pending_trail_stop = None

    def _reset_trade_state(self) -> None:
        super()._reset_trade_state()
        self._clear_structural_management()

    @staticmethod
    def _closed_execution_bar(bar: Bar) -> _ClosedExecutionBar:
        return _ClosedExecutionBar(
            ts_event=int(bar.ts_event),
            high=Decimal(str(bar.high)),
            low=Decimal(str(bar.low)),
            close=Decimal(str(bar.close)),
        )

    def _round_trip_reserve_per_unit(
        self,
        instrument: Any,
        entry: Decimal,
        candidate_stop: Decimal,
    ) -> Decimal:
        entry_slippage, stop_slippage = self._execution_reserves(instrument)
        return (
            entry_slippage
            + stop_slippage
            + entry * Decimal(str(self.config.estimated_entry_fee_rate))
            + candidate_stop * Decimal(str(self.config.estimated_stop_fee_rate))
            + entry * Decimal(str(self.config.estimated_funding_rate))
        )

    def _locks_positive_net(
        self,
        instrument: Any,
        side: Side,
        candidate_stop: Decimal,
    ) -> tuple[bool, Decimal]:
        entry = self._actual_entry_price
        if entry is None:
            return False, Decimal("0")
        reserve = self._round_trip_reserve_per_unit(instrument, entry, candidate_stop)
        tick = Decimal(str(instrument.price_increment))
        gross_locked = (
            candidate_stop - entry
            if side is Side.LONG
            else entry - candidate_stop
        )
        net_locked = gross_locked - reserve
        return net_locked >= tick, net_locked

    @staticmethod
    def _strict_swing_candidate(
        side: Side,
        left: _ClosedExecutionBar,
        center: _ClosedExecutionBar,
        right: _ClosedExecutionBar,
        tick: Decimal,
    ) -> tuple[Decimal, str] | None:
        if side is Side.LONG:
            if not (center.low < left.low and center.low < right.low):
                return None
            return center.low - tick, "CONFIRMED_SWING_LOW"
        if not (center.high > left.high and center.high > right.high):
            return None
        return center.high + tick, "CONFIRMED_SWING_HIGH"

    @staticmethod
    def _improves_stop(side: Side, candidate: Decimal, current: Decimal, tick: Decimal) -> bool:
        if side is Side.LONG:
            return candidate >= current + tick
        return candidate <= current - tick

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

        # The right-hand confirmation bar must not already have crossed the
        # proposed stop.  Strict swing geometry normally guarantees this; keep
        # the assertion explicit so a future detector change cannot introduce
        # an already-traded stop.
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
                self.active_stop_id,
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
                rule_provenance=STRUCTURAL_PROFIT_PROTECTION_RULE,
            )
        except Exception as exc:
            self._pending_trail_stop = None
            self._record(
                "structural_profit_stop_exception",
                plan_id=self.active_plan.plan_id,
                instrument_id=str(self.active_instrument_id),
                reason=repr(exc),
                rule_provenance=STRUCTURAL_PROFIT_PROTECTION_RULE,
            )
            self._request_emergency_flatten("structural_profit_stop_exception")

    def _flush_bar_bucket(self) -> None:
        execution_bars: list[tuple[InstrumentId, Bar]] = [
            (instrument_id, bar)
            for instrument_id, timeframe, bar in self.bar_bucket
            if timeframe == self.EXECUTION_MINUTES
        ]
        super()._flush_bar_bucket()
        if self.active_instrument_id is None:
            return
        for instrument_id, bar in sorted(
            execution_bars,
            key=lambda item: (int(item[1].ts_event), str(item[0])),
        ):
            if instrument_id == self.active_instrument_id:
                self._request_structural_stop(bar)

    def on_position_opened(self, event: PositionOpened) -> None:
        super().on_position_opened(event)
        if self.active_instrument_id is None or event.instrument_id != self.active_instrument_id:
            return
        self._trail_bars.clear()
        self._position_opened_ts_ns = int(event.ts_event)
        self._actual_entry_price = Decimal(str(event.avg_px_open))
        self._active_stop_trigger = (
            None if self.active_plan is None else Decimal(str(self.active_plan.stop))
        )
        self._pending_trail_stop = None
        self._record(
            "structural_profit_management_armed",
            plan_id=None if self.active_plan is None else self.active_plan.plan_id,
            instrument_id=str(event.instrument_id),
            actual_entry=float(self._actual_entry_price),
            initial_stop=(
                None if self._active_stop_trigger is None else float(self._active_stop_trigger)
            ),
            rule_provenance=STRUCTURAL_PROFIT_PROTECTION_RULE,
        )

    def on_order_updated(self, event: OrderUpdated) -> None:
        super().on_order_updated(event)
        if event.client_order_id != self.active_stop_id or event.trigger_price is None:
            return
        updated = Decimal(str(event.trigger_price))
        pending = self._pending_trail_stop
        self._active_stop_trigger = updated
        if pending is None:
            return
        instrument = (
            None
            if self.active_instrument_id is None
            else self.instruments.get(self.active_instrument_id)
        )
        tick = Decimal("0") if instrument is None else Decimal(str(instrument.price_increment))
        if abs(updated - pending) > tick / Decimal("2"):
            return
        self._pending_trail_stop = None
        self._record(
            "structural_profit_stop_confirmed",
            plan_id=None if self.active_plan is None else self.active_plan.plan_id,
            client_order_id=str(event.client_order_id),
            trigger_price=float(updated),
            event_ts_ns=int(event.ts_event),
            rule_provenance=STRUCTURAL_PROFIT_PROTECTION_RULE,
        )

    def on_order_modify_rejected(self, event: OrderModifyRejected) -> None:
        if event.client_order_id == self.active_stop_id:
            self._pending_trail_stop = None
        super().on_order_modify_rejected(event)


__all__ = [
    "EasyChartRE1StructuralStrategy",
    "STRUCTURAL_PROFIT_PROTECTION_RULE",
]
