"""Close-break plus next-hour hold for changes in the broad direction router.

Replacing the one-hour router with a four-hour trend label discarded useful
intraday information.  The actual translation error is narrower: a single
one-hour close through a confirmed swing was allowed to reverse the broad side
immediately, even though the EasyChart break grammar requires acceptance on the
next completed candle.

This module keeps the one-hour structure and every lower-frame decision intact.
Only a change of macro side is delayed until the next one-hour candle opens and
closes beyond the broken swing.  A same-direction break may refresh the latest
pivot immediately.  A failed opposite break leaves the existing macro side
unchanged.  Early lower-frame continuation can still trade against the old
macro side only when the already implemented BTC/ETH-led common factor supports
it, so genuine fast transitions are not simply forbidden.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import Pivot
from domain import Candle, Side
from easychart_re1_horizontal_flip_response import (
    EasyChartRE1HorizontalFlipResponseBundle,
)
from easychart_re1_local_auction_continuation import (
    EasyChartRE1LocalAuctionStrategy,
)


MACRO_BREAK_ACCEPTANCE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "SIXTY_MINUTE_DIRECTION_CHANGES_ONLY_AFTER_CLOSE_BREAK_AND_THE_NEXT_COMPLETED_HOUR_OPENS_AND_CLOSES_BEYOND_THE_BROKEN_SWING"
)
if MACRO_BREAK_ACCEPTANCE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (MACRO_BREAK_ACCEPTANCE_RULE,)


@dataclass(frozen=True, slots=True)
class PendingMacroBreak:
    side: Side
    pivot: Pivot
    break_time_ns: int
    break_close: float


class EasyChartRE1MacroAcceptanceBundle(EasyChartRE1HorizontalFlipResponseBundle):
    """Integrated execution policy with acceptance-confirmed macro changes."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._pending_macro_break: PendingMacroBreak | None = None
        self._macro_acceptance_counts: dict[str, int] = {}

    def _mainc(self, key: str) -> None:
        self._macro_acceptance_counts[key] = self._macro_acceptance_counts.get(key, 0) + 1

    @staticmethod
    def _held(pending: PendingMacroBreak, bar: Candle) -> bool:
        return (
            bar.open > pending.pivot.price and bar.close > pending.pivot.price
            if pending.side is Side.LONG
            else bar.open < pending.pivot.price and bar.close < pending.pivot.price
        )

    def _confirm_or_fail_pending_macro_break(self, bar: Candle) -> None:
        pending = self._pending_macro_break
        if pending is None:
            return
        self._pending_macro_break = None
        if not self._held(pending, bar):
            self._mainc("macro_break_failed_next_hour_hold")
            self._bundle_trace.append(
                {
                    "scenario_kind": "macro_break_failed_next_hour_hold",
                    "event_time_ns": bar.ts_close_ns,
                    "symbol": self.symbol,
                    "candidate_side": pending.side.name,
                    "pivot_id": pending.pivot.pivot_id,
                    "pivot_price": pending.pivot.price,
                    "break_time_ns": pending.break_time_ns,
                    "break_close": pending.break_close,
                    "hold_open": bar.open,
                    "hold_close": bar.close,
                    "retained_macro_side": None if self._macro_side is None else self._macro_side.name,
                    "rule_provenance": MACRO_BREAK_ACCEPTANCE_RULE,
                }
            )
            return

        changed = pending.side is not self._macro_side
        self._macro_side = pending.side
        self._last_direction_pivot = pending.pivot
        self._router_inc("htf_break_events")
        self._router_inc("htf_direction_changes" if changed else "htf_direction_refreshes")
        self._mainc("macro_break_accepted_next_hour_hold")
        self._bundle_trace.append(
            {
                "scenario_kind": "htf_structure_direction_break_accepted",
                "event_time_ns": bar.ts_close_ns,
                "symbol": self.symbol,
                "side": pending.side.name,
                "pivot_id": pending.pivot.pivot_id,
                "pivot_side": pending.pivot.side,
                "pivot_price": pending.pivot.price,
                "pivot_event_time_ns": pending.pivot.event_time_ns,
                "pivot_observed_time_ns": pending.pivot.observed_time_ns,
                "pivot_span": pending.pivot.span,
                "break_time_ns": pending.break_time_ns,
                "break_close": pending.break_close,
                "hold_open": bar.open,
                "hold_close": bar.close,
                "direction_changed": changed,
                "rule_provenance": MACRO_BREAK_ACCEPTANCE_RULE,
            }
        )

    def _arm_new_macro_break(self, bar: Candle) -> None:
        breaks = self._newly_broken_direction_pivots(bar)
        if not breaks:
            return
        side, pivot = max(
            breaks,
            key=lambda item: (
                item[1].event_time_ns,
                item[1].observed_time_ns,
                item[1].pivot_id,
            ),
        )
        if self._macro_side is side:
            self._last_direction_pivot = pivot
            self._router_inc("htf_break_events")
            self._router_inc("htf_direction_refreshes")
            self._mainc("same_side_macro_break_refreshed_without_flip_delay")
            self._bundle_trace.append(
                {
                    "scenario_kind": "htf_same_side_structure_break_refreshed",
                    "event_time_ns": bar.ts_close_ns,
                    "symbol": self.symbol,
                    "side": side.name,
                    "pivot_id": pivot.pivot_id,
                    "pivot_price": pivot.price,
                    "close": bar.close,
                    "rule_provenance": MACRO_BREAK_ACCEPTANCE_RULE,
                }
            )
            return

        self._pending_macro_break = PendingMacroBreak(
            side=side,
            pivot=pivot,
            break_time_ns=bar.ts_close_ns,
            break_close=bar.close,
        )
        self._mainc("opposite_or_neutral_macro_break_armed")
        self._bundle_trace.append(
            {
                "scenario_kind": "htf_structure_direction_break_waiting_acceptance",
                "event_time_ns": bar.ts_close_ns,
                "symbol": self.symbol,
                "candidate_side": side.name,
                "current_macro_side": None if self._macro_side is None else self._macro_side.name,
                "pivot_id": pivot.pivot_id,
                "pivot_price": pivot.price,
                "break_close": bar.close,
                "rule_provenance": MACRO_BREAK_ACCEPTANCE_RULE,
            }
        )

    def _advance_macro_direction(self, bar: Candle) -> None:
        # The current completed hour is the next-bar decision for an earlier
        # break.  Only after that decision may it arm a distinct new break.
        self._confirm_or_fail_pending_macro_break(bar)
        self._arm_new_macro_break(bar)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["macro_break_acceptance"] = {
            "counts": dict(sorted(self._macro_acceptance_counts.items())),
            "pending": None
            if self._pending_macro_break is None
            else {
                "side": self._pending_macro_break.side.name,
                "pivot_id": self._pending_macro_break.pivot.pivot_id,
                "pivot_price": self._pending_macro_break.pivot.price,
                "break_time_ns": self._pending_macro_break.break_time_ns,
            },
            "rule_provenance": MACRO_BREAK_ACCEPTANCE_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1MacroAcceptanceBundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
