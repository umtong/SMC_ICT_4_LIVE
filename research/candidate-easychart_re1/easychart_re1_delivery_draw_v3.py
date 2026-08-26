"""Fresh post-sweep structure cross for matching-scale delivery.

A previously broken internal pivot is not evidence that a new external sweep
transferred control.  The sweep event may inherit a pre-existing internal
reference only when its completed context close has not already crossed it.
Activation then requires a true later five-minute close cross: the previous
completed five-minute close was still on the old side and the current close is
on the intended side.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Candle, Side
from easychart_re1_delivery_draw import (
    EXTERNAL_SWEEP_SHIFT_RULE,
    PendingExternalTransfer,
)
from easychart_re1_delivery_draw_v2 import (
    CausalLiquidityDrawV2,
    STRICTLY_LATER_INTERNAL_SHIFT_RULE,
)


FRESH_POST_SWEEP_CROSS_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "EXTERNAL_SWEEP_CONTROL_TRANSFER_REQUIRES_A_TRUE_LATER_FIVE_MINUTE_CLOSE_CROSS_OF_AN_INTERNAL_PIVOT_NOT_ALREADY_BROKEN_AT_THE_SWEEP_CLOSE"
)
if FRESH_POST_SWEEP_CROSS_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (FRESH_POST_SWEEP_CROSS_RULE,)


class CausalLiquidityDrawV3(CausalLiquidityDrawV2):
    """Strict draw whose internal shift is a fresh post-event close cross."""

    def _new_pending_sweep(
        self,
        pivot: Any,
        side: Side,
        bar: Candle,
        mode: str,
    ) -> None:
        super()._new_pending_sweep(pivot, side, bar, mode)
        pending = self.pending
        if (
            pending is not None
            and pending.shift_price is not None
            and self._beyond(side, bar.close, pending.shift_price)
        ):
            self._inc("preexisting_internal_reference_already_broken_at_sweep")
            self._trace(
                "preexisting_internal_reference_already_broken_at_sweep",
                bar.ts_close_ns,
                side=side.name,
                source_pivot_id=pending.source_pivot_id,
                rejected_shift_pivot_id=pending.shift_pivot_id,
                rejected_shift_price=pending.shift_price,
                sweep_close=bar.close,
                rule_provenance=FRESH_POST_SWEEP_CROSS_RULE,
            )
            pending.shift_pivot_id = None
            pending.shift_price = None

    @staticmethod
    def _crossed(
        side: Side,
        previous_close: float,
        current_close: float,
        level: float,
    ) -> bool:
        if side is Side.LONG:
            return previous_close <= level and current_close > level
        return previous_close >= level and current_close < level

    def _on_decision(self, bar: Candle) -> None:
        self._advance_active(bar)
        previous_close = (
            None if not self.internal.bars else self.internal.bars[-1].close
        )
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
                        FRESH_POST_SWEEP_CROSS_RULE,
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
                        self._trace(
                            "post_sweep_internal_reference_confirmed",
                            bar.ts_close_ns,
                            side=pending.side.name,
                            source_pivot_id=pending.source_pivot_id,
                            shift_pivot_id=reference.pivot_id,
                            shift_price=reference.price,
                            rule_provenance=FRESH_POST_SWEEP_CROSS_RULE,
                        )
                if (
                    previous_close is not None
                    and pending.shift_price is not None
                    and self._crossed(
                        pending.side,
                        previous_close,
                        bar.close,
                        pending.shift_price,
                    )
                ):
                    self._inc("external_sweep_fresh_internal_cross_confirmed")
                    self._activate(pending, bar.ts_close_ns, bar.close)
                elif (
                    pending.shift_price is not None
                    and self._beyond(
                        pending.side,
                        bar.close,
                        pending.shift_price,
                    )
                ):
                    self._inc("internal_level_beyond_without_fresh_cross")
        self.internal.observe_price(bar)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["fresh_post_sweep_cross"] = {
            "policy": "PREVIOUS_5M_CLOSE_OLD_SIDE_CURRENT_5M_CLOSE_INTENDED_SIDE",
            "rule_provenance": FRESH_POST_SWEEP_CROSS_RULE,
        }
        return output
