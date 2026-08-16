"""Strictly causal activation and unspent-target binding for liquidity delivery.

The first delivery pass intentionally exposed the whole market-state idea before
adding secondary detail.  Two causal ownership corrections are necessary before
using its evidence:

* the five-minute internal shift must close strictly after the fifteen-minute
  external sweep; a same-timestamp constituent bar cannot confirm the event
  which contains it;
* an external draw already traded by the activation bar is spent even though
  the lifecycle book has not yet processed that completed bar.

This refinement changes only those two responsibilities.  The external span,
internal shift, accepted-break hold, source invalidation and matching-scale draw
remain unchanged.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import Pivot
from domain import Candle, Side
from easychart_re1_delivery_draw import (
    CausalLiquidityDraw,
    EXTERNAL_SWEEP_SHIFT_RULE,
    MATCHING_SCALE_LIQUIDITY_DRAW_RULE,
)


STRICTLY_LATER_INTERNAL_SHIFT_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "THE_INTERNAL_STRUCTURE_SHIFT_MUST_CLOSE_STRICTLY_AFTER_THE_EXTERNAL_SWEEP_TIMESTAMP"
)
ACTIVATION_BAR_UNSPENT_DRAW_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_MATCHING_SCALE_DRAW_MUST_REMAIN_BEYOND_THE_FULL_HIGH_LOW_RANGE_OF_THE_COMPLETED_ACTIVATION_BAR"
)
for _rule in (
    STRICTLY_LATER_INTERNAL_SHIFT_RULE,
    ACTIVATION_BAR_UNSPENT_DRAW_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class CausalLiquidityDrawV2(CausalLiquidityDraw):
    """Delivery state with strict event ordering and a genuinely live draw."""

    def __init__(self, symbol: str, tick_size: float) -> None:
        super().__init__(symbol, tick_size)
        self._current_completed_bar: Candle | None = None

    def _matching_target(
        self,
        side: Side,
        time_ns: int,
        current_price: float,
        source_pivot_id: str,
    ) -> Pivot | None:
        bar = self._current_completed_bar
        boundary = current_price
        if bar is not None and bar.ts_close_ns == time_ns:
            boundary = bar.high if side is Side.LONG else bar.low
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
                pivot.price > boundary
                if side is Side.LONG
                else pivot.price < boundary
            )
        ]
        if not candidates:
            self._inc("activation_bar_left_no_unspent_matching_draw")
            return None
        return (
            min(
                candidates,
                key=lambda item: (
                    item.price,
                    -item.event_time_ns,
                    item.pivot_id,
                ),
            )
            if side is Side.LONG
            else max(
                candidates,
                key=lambda item: (
                    item.price,
                    item.event_time_ns,
                    item.pivot_id,
                ),
            )
        )

    def _on_decision(self, bar: Candle) -> None:
        self._advance_active(bar)
        self.internal.on_bar(bar)
        pending = self.pending
        if (
            pending is not None
            and pending.mode != "EXTERNAL_ACCEPTANCE_WAITING_HOLD"
        ):
            if bar.ts_close_ns <= pending.event_time_ns:
                self._inc("pending_transfer_waiting_strictly_later_decision_close")
                self.internal.observe_price(bar)
                return

            invalidation = (
                pending.extreme - self.tick_size
                if pending.side is Side.LONG
                else pending.extreme + self.tick_size
            )
            if self._invalidation_touched(pending.side, bar, invalidation):
                self._inc("pending_external_transfer_invalidated")
                self._trace(
                    "pending_external_transfer_invalidated",
                    bar.ts_close_ns,
                    side=pending.side.name,
                    source_pivot_id=pending.source_pivot_id,
                    sweep_extreme=pending.extreme,
                    high=bar.high,
                    low=bar.low,
                    rule_provenance=(
                        EXTERNAL_SWEEP_SHIFT_RULE,
                        STRICTLY_LATER_INTERNAL_SHIFT_RULE,
                    ),
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
                    and self._beyond(
                        pending.side,
                        bar.close,
                        pending.shift_price,
                    )
                ):
                    self._inc("external_sweep_internal_shift_confirmed")
                    self._activate(pending, bar.ts_close_ns, bar.close)
        self.internal.observe_price(bar)

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> None:
        self._current_completed_bar = bar
        try:
            super().on_bar(timeframe_minutes, bar)
        finally:
            self._current_completed_bar = None

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["strict_causal_activation"] = {
            "internal_shift": "STRICTLY_LATER_THAN_EXTERNAL_SWEEP",
            "target_state": "BEYOND_FULL_ACTIVATION_BAR_RANGE",
            "rules": (
                MATCHING_SCALE_LIQUIDITY_DRAW_RULE,
                STRICTLY_LATER_INTERNAL_SHIFT_RULE,
                ACTIVATION_BAR_UNSPENT_DRAW_RULE,
            ),
        }
        return output
