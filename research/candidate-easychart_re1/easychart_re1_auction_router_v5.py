"""Keep a failed broad break pivot eligible for a later genuine acceptance.

The acceptance-confirmed one-hour router correctly delayed direction changes,
but it inherited the base break detector's one-shot pivot consumption.  The
pivot ID was marked broken on the first close through it even when the required
next-hour hold failed.  A later clean close-and-hold of the same still-relevant
swing could therefore never change context.

A failed acceptance is evidence that the break was rejected, not that the
reference swing ceased to exist.  This correction returns only that pivot ID to
the eligible set before the current completed hour is considered as a possible
new break bar.  Accepted and same-side refreshed pivots remain consumed.  No
trading signal, threshold, target, stop, risk or account rule changes.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Candle
from easychart_re1_auction_router_v4 import EasyChartRE1AuctionRouterV4Bundle
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy


FAILED_MACRO_BREAK_REUSABLE_PIVOT_RULE = (
    "RESEARCH_IMPLEMENTATION:"
    "A_SIXTY_MINUTE_SWING_PIVOT_WHOSE_BREAK_FAILS_THE_REQUIRED_NEXT_HOUR_HOLD_REMAINS_ELIGIBLE_FOR_A_LATER_CLOSE_AND_HOLD"
)
if FAILED_MACRO_BREAK_REUSABLE_PIVOT_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (FAILED_MACRO_BREAK_REUSABLE_PIVOT_RULE,)


class ReusableFailedMacroBreakMixin:
    def _confirm_or_fail_pending_macro_break(self, bar: Candle) -> None:
        pending = self._pending_macro_break
        failed = pending is not None and not self._held(pending, bar)
        if failed:
            self._broken_direction_pivot_ids.discard(pending.pivot.pivot_id)
            self._mainc("failed_macro_break_pivot_returned_to_eligible_set")
        super()._confirm_or_fail_pending_macro_break(bar)


class EasyChartRE1AuctionRouterV5Bundle(
    ReusableFailedMacroBreakMixin,
    EasyChartRE1AuctionRouterV4Bundle,
):
    """Specifically-owned integrated policy with correct macro pivot lifecycle."""

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["failed_macro_break_pivot_lifecycle"] = {
            "failed_break_pivot_reusable": True,
            "rule_provenance": FAILED_MACRO_BREAK_REUSABLE_PIVOT_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1AuctionRouterV5Bundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
