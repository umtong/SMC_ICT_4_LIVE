"""Close-held first pullback for the nested local continuation auction.

The nested continuation already requires the *next* completed one-minute candle
to extend beyond the pullback extreme.  Requiring the pullback candle itself to
be green for a long or red for a short duplicates that directional proof and
misclassifies an ordinary counter-colour pullback which dips into fair value,
closes back on the valid side, then resumes.

This engine changes only that translation.  The first touch is still consumed;
it must not invalidate the source, spend the objective, or close through the
source OB / anchored fair-value boundary.  The inherited immediate next-candle
response, structural stop, first obstacle target and 1R minimum remain intact.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle, Side
from easychart_re1_local_continuation import (
    LocalAuctionContinuationEngine,
    MinuteWeight,
)


CLOSE_HELD_PULLBACK_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:THE_FIRST_LOCAL_CONTINUATION_PULLBACK_MAY_BE_"
    "A_COUNTER_COLOUR_CANDLE_WHEN_IT_CLOSES_BACK_ON_THE_VALID_SIDE_AND_THE_"
    "IMMEDIATE_NEXT_COMPLETED_MINUTE_STILL_PROVES_DIRECTIONAL_RESPONSE"
)
if CLOSE_HELD_PULLBACK_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CLOSE_HELD_PULLBACK_RULE,)


class CloseHeldLocalAuctionContinuationEngine(LocalAuctionContinuationEngine):
    """Use close-location for the pullback and keep inherited response proof."""

    def _advance_setup(
        self,
        bar: Candle,
        minute: MinuteWeight,
    ) -> list[V5TradePlan]:
        setup = self._active
        if setup is None or setup.state != "WAITING_PULLBACK":
            return super()._advance_setup(bar, minute)
        if bar.ts_close_ns <= setup.impulse_time_ns:
            return []

        self._update_vwap(setup, minute)
        if self._target_touched(setup, bar):
            self._finish(
                setup,
                "local_continuation_target_spent_before_entry",
                bar.ts_close_ns,
            )
            return []
        if self._zone_invalidated(setup, bar):
            self._finish(
                setup,
                "local_continuation_source_invalidated_before_entry",
                bar.ts_close_ns,
            )
            return []

        choice = self._pullback_choice(setup, bar)
        if choice is None:
            return []
        kind, lower, upper = choice
        held = (
            bar.close > upper
            if setup.side is Side.LONG
            else bar.close < lower
        )
        if not held:
            self._finish(
                setup,
                "local_continuation_first_pullback_failed",
                bar.ts_close_ns,
                pullback_kind=kind.value,
                pullback_lower=lower,
                pullback_upper=upper,
                pullback_open=bar.open,
                pullback_high=bar.high,
                pullback_low=bar.low,
                pullback_close=bar.close,
                rule_provenance=CLOSE_HELD_PULLBACK_RULE,
            )
            return []

        setup.state = "WAITING_RESPONSE"
        setup.retest_time_ns = bar.ts_close_ns
        setup.retest_high = bar.high
        setup.retest_low = bar.low
        setup.retest_kind = kind
        self._inc("local_continuation_pullback_confirmed")
        self._record(
            "local_continuation_pullback_confirmed",
            bar.ts_close_ns,
            setup_id=setup.setup_id,
            side=setup.side.name,
            pullback_kind=kind.value,
            pullback_lower=lower,
            pullback_upper=upper,
            pullback_open=bar.open,
            pullback_high=bar.high,
            pullback_low=bar.low,
            pullback_close=bar.close,
            anchored_vwap=setup.anchored_vwap,
            rule_provenance=CLOSE_HELD_PULLBACK_RULE,
        )
        return []

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["close_held_pullback"] = {
            "pullback_direction": "CLOSE_LOCATION_NOT_CANDLE_COLOUR",
            "response": "INHERITED_IMMEDIATE_NEXT_COMPLETED_MINUTE",
            "rule_provenance": CLOSE_HELD_PULLBACK_RULE,
        }
        return output


MultiScaleScenarioBundle = CloseHeldLocalAuctionContinuationEngine
