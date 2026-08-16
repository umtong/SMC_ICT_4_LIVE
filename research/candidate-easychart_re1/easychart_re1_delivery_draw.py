"""Causal matching-scale liquidity draw for the EasyChart RE1 day-trade policy.

Direction is not a permanent trend label.  The supplied market-structure
material defines order flow by two observable facts: which external liquidity
has just been taken, and which matching-scale external liquidity remains as the
next draw.  Small internal pivots encountered while that delivery is active are
normally low-resistance waypoints, not independent reversal reasons.

This module translates that auction into one compact causal state machine:

* causally confirmed 15-minute span-6 wick swings represent external liquidity;
* a body close through external liquidity must survive the next completed
  15-minute close before it becomes accepted delivery;
* a wick sweep which closes back inside becomes delivery only after a later
  completed five-minute close breaks the most recent confirmed internal swing
  in the intended direction;
* the state is active only when a still-unspent opposite span-6 swing exists as
  a matching-scale draw, and ends when that draw or the source invalidation is
  traded.

There is no fitted distance, score, clock expiry, volatility threshold, session
rule or outcome information.  Every event uses completed bars and pivots whose
right-side confirmation was already available.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from causal_lifecycle_v5 import LifecycleAwareStructureBook
from contracts_v5 import Pivot, V5TradePlan
from domain import Candle, Side
from execution_re1_factor_persistence import (
    CommonAuctionRegime,
    CommonAuctionSnapshot,
)


MATCHING_SCALE_LIQUIDITY_DRAW_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ORDER_FLOW_BEGINS_AFTER_CONFIRMED_EXTERNAL_LIQUIDITY_TRANSFER_AND_REMAINS_ACTIVE_ONLY_TOWARD_A_STILL_UNSPENT_MATCHING_SCALE_EXTERNAL_DRAW"
)
EXTERNAL_SWEEP_SHIFT_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_FIFTEEN_MINUTE_EXTERNAL_SWEEP_REQUIRES_A_LATER_FIVE_MINUTE_CLOSE_THROUGH_THE_MOST_RECENT_CONFIRMED_INTERNAL_SWING"
)
EXTERNAL_BREAK_HOLD_RULE = (
    "SOURCE_EXPLICIT:"
    "A_BODY_BREAK_OF_EXTERNAL_LIQUIDITY_REQUIRES_THE_NEXT_COMPLETED_CONTEXT_BAR_TO_HOLD_OUTSIDE"
)
for _rule in (
    MATCHING_SCALE_LIQUIDITY_DRAW_RULE,
    EXTERNAL_SWEEP_SHIFT_RULE,
    EXTERNAL_BREAK_HOLD_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


@dataclass(slots=True)
class PendingExternalTransfer:
    side: Side
    mode: str
    source_pivot_id: str
    source_pivot_side: str
    source_pivot_price: float
    source_event_time_ns: int
    event_time_ns: int
    extreme: float
    shift_pivot_id: str | None
    shift_price: float | None


@dataclass(frozen=True, slots=True)
class ActiveLiquidityDraw:
    side: Side
    event_time_ns: int
    source_pivot_id: str
    source_pivot_price: float
    source_mode: str
    invalidation: float
    shift_pivot_id: str | None
    target_pivot_id: str
    target_price: float


class CausalLiquidityDraw:
    """One-symbol external-liquidity acquisition and delivery state."""

    CONTEXT_MINUTES = 15
    DECISION_MINUTES = 5
    EXTERNAL_SPAN = 6
    INTERNAL_SPAN = 2

    def __init__(self, symbol: str, tick_size: float) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.external = LifecycleAwareStructureBook(
            symbol,
            self.CONTEXT_MINUTES,
            tick_size,
            pivot_spans=(self.INTERNAL_SPAN, self.EXTERNAL_SPAN),
        )
        self.internal = LifecycleAwareStructureBook(
            symbol,
            self.DECISION_MINUTES,
            tick_size,
            pivot_spans=(self.INTERNAL_SPAN, self.EXTERNAL_SPAN),
        )
        self.pending: PendingExternalTransfer | None = None
        self.active: ActiveLiquidityDraw | None = None
        self._handled_external_pivots: set[str] = set()
        self._counts: dict[str, int] = {}
        self._trace_records: list[dict[str, Any]] = []

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _trace(self, kind: str, time_ns: int, **values: Any) -> None:
        self._trace_records.append(
            {
                "scenario_kind": kind,
                "event_time_ns": time_ns,
                "symbol": self.symbol,
                **values,
            }
        )

    @staticmethod
    def _wanted_internal_side(side: Side) -> str:
        return "HIGH" if side is Side.LONG else "LOW"

    @staticmethod
    def _target_pivot_side(side: Side) -> str:
        return "HIGH" if side is Side.LONG else "LOW"

    @staticmethod
    def _beyond(side: Side, price: float, level: float) -> bool:
        return price > level if side is Side.LONG else price < level

    @staticmethod
    def _target_touched(side: Side, bar: Candle, target: float) -> bool:
        return bar.high >= target if side is Side.LONG else bar.low <= target

    @staticmethod
    def _invalidation_touched(side: Side, bar: Candle, invalidation: float) -> bool:
        return bar.low <= invalidation if side is Side.LONG else bar.high >= invalidation

    def _latest_internal_reference(
        self,
        side: Side,
        time_ns: int,
        *,
        event_floor_ns: int | None = None,
    ) -> Pivot | None:
        wanted = self._wanted_internal_side(side)
        candidates = [
            pivot
            for pivot in self.internal.pivots
            if pivot.span == self.INTERNAL_SPAN
            and pivot.side == wanted
            and pivot.observed_time_ns < time_ns
            and (
                event_floor_ns is None
                or pivot.event_time_ns >= event_floor_ns
            )
        ]
        return max(
            candidates,
            key=lambda item: (
                item.event_time_ns,
                item.observed_time_ns,
                item.pivot_id,
            ),
            default=None,
        )

    def _matching_target(
        self,
        side: Side,
        time_ns: int,
        current_price: float,
        source_pivot_id: str,
    ) -> Pivot | None:
        wanted = self._target_pivot_side(side)
        candidates = [
            pivot
            for pivot in self.external.pivots
            if pivot.span == self.EXTERNAL_SPAN
            and pivot.side == wanted
            and pivot.pivot_id != source_pivot_id
            and pivot.observed_time_ns < time_ns
            and not (
                pivot.consumed_time_ns is not None
                and pivot.consumed_time_ns < time_ns
            )
            and (
                pivot.price > current_price
                if side is Side.LONG
                else pivot.price < current_price
            )
        ]
        if not candidates:
            return None
        return (
            min(candidates, key=lambda item: (item.price, -item.event_time_ns, item.pivot_id))
            if side is Side.LONG
            else max(candidates, key=lambda item: (item.price, item.event_time_ns, item.pivot_id))
        )

    def _activate(
        self,
        pending: PendingExternalTransfer,
        time_ns: int,
        current_price: float,
    ) -> None:
        target = self._matching_target(
            pending.side,
            time_ns,
            current_price,
            pending.source_pivot_id,
        )
        if target is None:
            self._inc("confirmed_transfer_without_matching_external_draw")
            self._trace(
                "confirmed_transfer_without_matching_external_draw",
                time_ns,
                side=pending.side.name,
                mode=pending.mode,
                source_pivot_id=pending.source_pivot_id,
                source_pivot_price=pending.source_pivot_price,
                rule_provenance=MATCHING_SCALE_LIQUIDITY_DRAW_RULE,
            )
            self.pending = None
            self.active = None
            return

        invalidation = (
            pending.extreme - self.tick_size
            if pending.side is Side.LONG
            else pending.extreme + self.tick_size
        )
        self.active = ActiveLiquidityDraw(
            side=pending.side,
            event_time_ns=time_ns,
            source_pivot_id=pending.source_pivot_id,
            source_pivot_price=pending.source_pivot_price,
            source_mode=pending.mode,
            invalidation=invalidation,
            shift_pivot_id=pending.shift_pivot_id,
            target_pivot_id=target.pivot_id,
            target_price=target.price,
        )
        self.pending = None
        self._inc("matching_scale_liquidity_draw_activated")
        self._inc(f"draw_activated_{self.active.side.name.lower()}")
        self._trace(
            "matching_scale_liquidity_draw_activated",
            time_ns,
            side=self.active.side.name,
            source_mode=self.active.source_mode,
            source_pivot_id=self.active.source_pivot_id,
            source_pivot_price=self.active.source_pivot_price,
            shift_pivot_id=self.active.shift_pivot_id,
            invalidation=self.active.invalidation,
            target_pivot_id=self.active.target_pivot_id,
            target_price=self.active.target_price,
            rule_provenance=(
                MATCHING_SCALE_LIQUIDITY_DRAW_RULE,
                EXTERNAL_SWEEP_SHIFT_RULE,
                EXTERNAL_BREAK_HOLD_RULE,
            ),
        )

    def _end_active(self, bar: Candle, reason: str) -> None:
        active = self.active
        if active is None:
            return
        self._inc(reason)
        self._trace(
            reason,
            bar.ts_close_ns,
            side=active.side.name,
            source_pivot_id=active.source_pivot_id,
            target_pivot_id=active.target_pivot_id,
            target_price=active.target_price,
            invalidation=active.invalidation,
            close=bar.close,
            high=bar.high,
            low=bar.low,
            rule_provenance=MATCHING_SCALE_LIQUIDITY_DRAW_RULE,
        )
        self.active = None

    def _advance_active(self, bar: Candle) -> None:
        active = self.active
        if active is None:
            return
        if self._target_touched(active.side, bar, active.target_price):
            self._end_active(bar, "matching_external_draw_reached")
            return
        if self._invalidation_touched(active.side, bar, active.invalidation):
            self._end_active(bar, "delivery_source_invalidation_reached")

    def _new_pending_sweep(
        self,
        pivot: Pivot,
        side: Side,
        bar: Candle,
        mode: str,
    ) -> None:
        reference = self._latest_internal_reference(side, bar.ts_close_ns)
        extreme = bar.low if side is Side.LONG else bar.high
        self.pending = PendingExternalTransfer(
            side=side,
            mode=mode,
            source_pivot_id=pivot.pivot_id,
            source_pivot_side=pivot.side,
            source_pivot_price=pivot.price,
            source_event_time_ns=pivot.event_time_ns,
            event_time_ns=bar.ts_close_ns,
            extreme=extreme,
            shift_pivot_id=None if reference is None else reference.pivot_id,
            shift_price=None if reference is None else reference.price,
        )
        self._inc("external_sweep_waiting_internal_shift")
        self._trace(
            "external_sweep_waiting_internal_shift",
            bar.ts_close_ns,
            side=side.name,
            mode=mode,
            source_pivot_id=pivot.pivot_id,
            source_pivot_side=pivot.side,
            source_pivot_price=pivot.price,
            sweep_extreme=extreme,
            shift_pivot_id=None if reference is None else reference.pivot_id,
            shift_price=None if reference is None else reference.price,
            rule_provenance=EXTERNAL_SWEEP_SHIFT_RULE,
        )

    def _new_pending_acceptance(
        self,
        pivot: Pivot,
        side: Side,
        bar: Candle,
    ) -> None:
        extreme = bar.low if side is Side.LONG else bar.high
        self.pending = PendingExternalTransfer(
            side=side,
            mode="EXTERNAL_ACCEPTANCE_WAITING_HOLD",
            source_pivot_id=pivot.pivot_id,
            source_pivot_side=pivot.side,
            source_pivot_price=pivot.price,
            source_event_time_ns=pivot.event_time_ns,
            event_time_ns=bar.ts_close_ns,
            extreme=extreme,
            shift_pivot_id=None,
            shift_price=None,
        )
        self._inc("external_acceptance_waiting_next_context_hold")
        self._trace(
            "external_acceptance_waiting_next_context_hold",
            bar.ts_close_ns,
            side=side.name,
            source_pivot_id=pivot.pivot_id,
            source_pivot_side=pivot.side,
            source_pivot_price=pivot.price,
            break_close=bar.close,
            break_high=bar.high,
            break_low=bar.low,
            rule_provenance=EXTERNAL_BREAK_HOLD_RULE,
        )

    def _advance_acceptance_hold(self, bar: Candle) -> None:
        pending = self.pending
        if (
            pending is None
            or pending.mode != "EXTERNAL_ACCEPTANCE_WAITING_HOLD"
            or bar.ts_close_ns <= pending.event_time_ns
        ):
            return
        held = self._beyond(pending.side, bar.close, pending.source_pivot_price)
        if held:
            if pending.side is Side.LONG:
                pending.extreme = min(pending.extreme, bar.low)
            else:
                pending.extreme = max(pending.extreme, bar.high)
            pending.mode = "EXTERNAL_ACCEPTANCE_HELD"
            self._inc("external_acceptance_next_context_hold_confirmed")
            self._activate(pending, bar.ts_close_ns, bar.close)
            return

        # A failed accepted break which closes back through the same external
        # level is now a sweep candidate in the opposite direction.  It still
        # needs an internal structure shift before delivery can begin.
        failed_side = Side.SHORT if pending.side is Side.LONG else Side.LONG
        pivot = next(
            (
                item
                for item in self.external.pivots
                if item.pivot_id == pending.source_pivot_id
            ),
            None,
        )
        if pivot is not None:
            self._inc("external_acceptance_failed_back_inside")
            self._new_pending_sweep(
                pivot,
                failed_side,
                bar,
                "FAILED_EXTERNAL_BREAK_SWEEP",
            )
        else:
            self.pending = None

    def _external_candidates(self, bar: Candle) -> list[Pivot]:
        return [
            pivot
            for pivot in self.external.pivots
            if pivot.span == self.EXTERNAL_SPAN
            and pivot.pivot_id not in self._handled_external_pivots
            and pivot.observed_time_ns < bar.ts_close_ns
            and (
                bar.high > pivot.price
                if pivot.side == "HIGH"
                else bar.low < pivot.price
            )
        ]

    def _discover_external_transfer(self, bar: Candle) -> None:
        candidates = self._external_candidates(bar)
        if not candidates:
            return
        high_candidates = [item for item in candidates if item.side == "HIGH"]
        low_candidates = [item for item in candidates if item.side == "LOW"]
        for pivot in candidates:
            self._handled_external_pivots.add(pivot.pivot_id)

        if high_candidates and low_candidates:
            self.pending = None
            self.active = None
            self._inc("context_bar_took_both_external_sides_unresolved")
            self._trace(
                "context_bar_took_both_external_sides_unresolved",
                bar.ts_close_ns,
                high_pivot_ids=[item.pivot_id for item in high_candidates],
                low_pivot_ids=[item.pivot_id for item in low_candidates],
                high=bar.high,
                low=bar.low,
                close=bar.close,
                rule_provenance=MATCHING_SCALE_LIQUIDITY_DRAW_RULE,
            )
            return

        group = high_candidates or low_candidates
        pivot = max(
            group,
            key=lambda item: (
                item.event_time_ns,
                item.observed_time_ns,
                -abs(item.price - bar.open),
                item.pivot_id,
            ),
        )
        if pivot.side == "HIGH":
            if bar.close > pivot.price:
                self._new_pending_acceptance(pivot, Side.LONG, bar)
            else:
                self._new_pending_sweep(
                    pivot,
                    Side.SHORT,
                    bar,
                    "EXTERNAL_HIGH_SWEEP_RECLAIM",
                )
        else:
            if bar.close < pivot.price:
                self._new_pending_acceptance(pivot, Side.SHORT, bar)
            else:
                self._new_pending_sweep(
                    pivot,
                    Side.LONG,
                    bar,
                    "EXTERNAL_LOW_SWEEP_RECLAIM",
                )

    def _on_context(self, bar: Candle) -> None:
        self._advance_active(bar)
        self.external.on_bar(bar)
        self._advance_acceptance_hold(bar)
        self._discover_external_transfer(bar)
        self.external.observe_price(bar)

    def _on_decision(self, bar: Candle) -> None:
        self._advance_active(bar)
        self.internal.on_bar(bar)
        pending = self.pending
        if (
            pending is not None
            and pending.mode != "EXTERNAL_ACCEPTANCE_WAITING_HOLD"
        ):
            if self._invalidation_touched(pending.side, bar, (
                pending.extreme - self.tick_size
                if pending.side is Side.LONG
                else pending.extreme + self.tick_size
            )):
                self._inc("pending_external_transfer_invalidated")
                self._trace(
                    "pending_external_transfer_invalidated",
                    bar.ts_close_ns,
                    side=pending.side.name,
                    source_pivot_id=pending.source_pivot_id,
                    sweep_extreme=pending.extreme,
                    high=bar.high,
                    low=bar.low,
                    rule_provenance=EXTERNAL_SWEEP_SHIFT_RULE,
                )
                self.pending = None
            else:
                if pending.shift_price is None:
                    reference = self._latest_internal_reference(
                        pending.side,
                        bar.ts_close_ns,
                        event_floor_ns=pending.event_time_ns,
                    )
                    if reference is not None:
                        pending.shift_pivot_id = reference.pivot_id
                        pending.shift_price = reference.price
                        self._inc("post_sweep_internal_reference_confirmed")
                if (
                    pending.shift_price is not None
                    and self._beyond(pending.side, bar.close, pending.shift_price)
                ):
                    self._inc("external_sweep_internal_shift_confirmed")
                    self._activate(pending, bar.ts_close_ns, bar.close)
        self.internal.observe_price(bar)

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> None:
        if timeframe_minutes == self.CONTEXT_MINUTES:
            self._on_context(bar)
        elif timeframe_minutes == self.DECISION_MINUTES:
            self._on_decision(bar)

    def allows(self, plan: V5TradePlan) -> bool:
        active = self.active
        return bool(
            active is not None
            and plan.side is active.side
            and plan.observed_time_ns >= active.event_time_ns
        )

    @property
    def common_snapshot(self) -> CommonAuctionSnapshot:
        active = self.active
        if active is None:
            return CommonAuctionSnapshot(
                CommonAuctionRegime.UNKNOWN,
                None,
                0,
                0,
                None,
                (),
                None,
                None,
            )
        return CommonAuctionSnapshot(
            CommonAuctionRegime.PERSISTENT,
            active.side,
            0,
            1,
            active.event_time_ns,
            (self.symbol,),
            active.side,
            active.event_time_ns,
        )

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self._trace_records
        self._trace_records = []
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        active = self.active
        pending = self.pending
        return {
            "counts": dict(sorted(self._counts.items())),
            "active": None
            if active is None
            else {
                "side": active.side.name,
                "event_time_ns": active.event_time_ns,
                "source_mode": active.source_mode,
                "source_pivot_id": active.source_pivot_id,
                "invalidation": active.invalidation,
                "target_pivot_id": active.target_pivot_id,
                "target_price": active.target_price,
            },
            "pending": None
            if pending is None
            else {
                "side": pending.side.name,
                "mode": pending.mode,
                "source_pivot_id": pending.source_pivot_id,
                "source_pivot_price": pending.source_pivot_price,
                "event_time_ns": pending.event_time_ns,
                "extreme": pending.extreme,
                "shift_pivot_id": pending.shift_pivot_id,
                "shift_price": pending.shift_price,
            },
            "external_structure": dict(self.external.diagnostics),
            "internal_structure": dict(self.internal.diagnostics),
            "rules": (
                MATCHING_SCALE_LIQUIDITY_DRAW_RULE,
                EXTERNAL_SWEEP_SHIFT_RULE,
                EXTERNAL_BREAK_HOLD_RULE,
            ),
        }
