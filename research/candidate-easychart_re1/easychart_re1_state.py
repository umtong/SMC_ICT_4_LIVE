"""Causal market-state router for the complete EasyChart RE1 policy.

The previous router labelled direction whenever price crossed any still-unmarked
old span-2 pivot.  On a 15-minute chart this produced hundreds of apparent BOS
changes per month and did not represent the larger auction a human calls market
structure.

This module keeps one current confirmed swing high and one current confirmed
swing low.  A close can break each reference only once; after that, the router
waits for a later causally confirmed reference on that side.  Span-6 swings are
used for 15-minute and 60-minute direction, while smaller pivots remain entry
geometry and objectives.  A pivot observed on the current bar cannot be broken
until a later completed bar.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import Pivot
from domain import Candle, Side
from easychart_re1_wedge import EasyChartRE1WedgeBundle


CAUSAL_STRUCTURE_STATE_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "DIRECTION_BREAKS_ONE_CURRENT_CONFIRMED_SPAN6_SWING_REFERENCE_AND_WAITS_FOR_A_LATER_REFERENCE"
)
if CAUSAL_STRUCTURE_STATE_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (CAUSAL_STRUCTURE_STATE_RULE,)


class EasyChartRE1StateBundle(EasyChartRE1WedgeBundle):
    """Human-entry/wedge policy routed by one causal structural state."""

    DIRECTION_PIVOT_SPAN = 6
    LOCAL_DIRECTION_PIVOT_SPAN = 6

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._macro_reference_high: Pivot | None = None
        self._macro_reference_low: Pivot | None = None
        self._macro_high_watermark_ns = -1
        self._macro_low_watermark_ns = -1
        self._local_reference_high: Pivot | None = None
        self._local_reference_low: Pivot | None = None
        self._local_high_watermark_ns = -1
        self._local_low_watermark_ns = -1
        self._state_counts: dict[str, int] = {}

    def _sinc(self, key: str) -> None:
        self._state_counts[key] = self._state_counts.get(key, 0) + 1

    @staticmethod
    def _latest_reference(
        pivots: list[Pivot],
        *,
        side: str,
        span: int,
        before_time_ns: int,
        after_event_time_ns: int,
    ) -> Pivot | None:
        return max(
            (
                pivot
                for pivot in pivots
                if pivot.side == side
                and pivot.span == span
                and pivot.observed_time_ns < before_time_ns
                and pivot.event_time_ns > after_event_time_ns
            ),
            key=lambda item: (item.event_time_ns, item.observed_time_ns, item.pivot_id),
            default=None,
        )

    def _refresh_macro_references(self, bar: Candle) -> None:
        high = self._latest_reference(
            self.macro_structure.pivots,
            side="HIGH",
            span=self.DIRECTION_PIVOT_SPAN,
            before_time_ns=bar.ts_close_ns,
            after_event_time_ns=self._macro_high_watermark_ns,
        )
        low = self._latest_reference(
            self.macro_structure.pivots,
            side="LOW",
            span=self.DIRECTION_PIVOT_SPAN,
            before_time_ns=bar.ts_close_ns,
            after_event_time_ns=self._macro_low_watermark_ns,
        )
        if high is not None and (
            self._macro_reference_high is None
            or high.event_time_ns > self._macro_reference_high.event_time_ns
        ):
            self._macro_reference_high = high
            self._sinc("macro_reference_high_updated")
        if low is not None and (
            self._macro_reference_low is None
            or low.event_time_ns > self._macro_reference_low.event_time_ns
        ):
            self._macro_reference_low = low
            self._sinc("macro_reference_low_updated")

    def _refresh_local_references(self, bar: Candle) -> None:
        high = self._latest_reference(
            self.local_direction_structure.pivots,
            side="HIGH",
            span=self.LOCAL_DIRECTION_PIVOT_SPAN,
            before_time_ns=bar.ts_close_ns,
            after_event_time_ns=self._local_high_watermark_ns,
        )
        low = self._latest_reference(
            self.local_direction_structure.pivots,
            side="LOW",
            span=self.LOCAL_DIRECTION_PIVOT_SPAN,
            before_time_ns=bar.ts_close_ns,
            after_event_time_ns=self._local_low_watermark_ns,
        )
        if high is not None and (
            self._local_reference_high is None
            or high.event_time_ns > self._local_reference_high.event_time_ns
        ):
            self._local_reference_high = high
            self._sinc("local_reference_high_updated")
        if low is not None and (
            self._local_reference_low is None
            or low.event_time_ns > self._local_reference_low.event_time_ns
        ):
            self._local_reference_low = low
            self._sinc("local_reference_low_updated")

    @staticmethod
    def _choose_break(
        bar: Candle,
        high: Pivot | None,
        low: Pivot | None,
    ) -> tuple[Side, Pivot] | None:
        candidates: list[tuple[Side, Pivot]] = []
        if high is not None and bar.close > high.price:
            candidates.append((Side.LONG, high))
        if low is not None and bar.close < low.price:
            candidates.append((Side.SHORT, low))
        return max(
            candidates,
            key=lambda item: (item[1].event_time_ns, item[1].observed_time_ns, item[1].pivot_id),
            default=None,
        )

    def _advance_macro_direction(self, bar: Candle) -> None:
        self._refresh_macro_references(bar)
        broken = self._choose_break(bar, self._macro_reference_high, self._macro_reference_low)
        if broken is None:
            return
        side, pivot = broken
        changed = side is not self._macro_side
        self._macro_side = side
        self._last_direction_pivot = pivot
        if side is Side.LONG:
            self._macro_high_watermark_ns = pivot.event_time_ns
            self._macro_reference_high = None
        else:
            self._macro_low_watermark_ns = pivot.event_time_ns
            self._macro_reference_low = None
        self._router_inc("htf_break_events")
        self._sinc("macro_reference_breaks")
        if changed:
            self._router_inc("htf_direction_changes")
            self._sinc("macro_direction_changes")
        self._bundle_trace.append(
            {
                "scenario_kind": "htf_structure_direction_break",
                "event_time_ns": bar.ts_close_ns,
                "symbol": self.symbol,
                "side": side.name,
                "pivot_id": pivot.pivot_id,
                "pivot_side": pivot.side,
                "pivot_price": pivot.price,
                "pivot_event_time_ns": pivot.event_time_ns,
                "pivot_observed_time_ns": pivot.observed_time_ns,
                "pivot_span": pivot.span,
                "close": bar.close,
                "direction_changed": changed,
                "rule_provenance": CAUSAL_STRUCTURE_STATE_RULE,
            }
        )

    def _advance_local_direction(self, bar: Candle) -> None:
        self._refresh_local_references(bar)
        broken = self._choose_break(bar, self._local_reference_high, self._local_reference_low)
        if broken is None:
            return
        side, pivot = broken
        changed = side is not self._local_side
        self._local_side = side
        self._last_local_direction_pivot = pivot
        self._local_break_time_ns = bar.ts_close_ns
        if side is Side.LONG:
            self._local_high_watermark_ns = pivot.event_time_ns
            self._local_reference_high = None
        else:
            self._local_low_watermark_ns = pivot.event_time_ns
            self._local_reference_low = None
        self._local_inc("local_bos_events")
        self._sinc("local_reference_breaks")
        if changed:
            self._local_inc("local_direction_changes")
            self._sinc("local_direction_changes")
        self._bundle_trace.append(
            {
                "scenario_kind": "local_fifteen_minute_direction_break",
                "event_time_ns": bar.ts_close_ns,
                "symbol": self.symbol,
                "side": side.name,
                "pivot_id": pivot.pivot_id,
                "pivot_side": pivot.side,
                "pivot_price": pivot.price,
                "pivot_event_time_ns": pivot.event_time_ns,
                "pivot_observed_time_ns": pivot.observed_time_ns,
                "pivot_span": pivot.span,
                "close": bar.close,
                "direction_changed": changed,
                "rule_provenance": CAUSAL_STRUCTURE_STATE_RULE,
            }
        )

    @staticmethod
    def _pivot_record(pivot: Pivot | None) -> dict[str, Any] | None:
        if pivot is None:
            return None
        return {
            "pivot_id": pivot.pivot_id,
            "side": pivot.side,
            "price": pivot.price,
            "event_time_ns": pivot.event_time_ns,
            "observed_time_ns": pivot.observed_time_ns,
            "span": pivot.span,
        }

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["causal_market_state_policy"] = {
            "macro_side": "NEUTRAL" if self._macro_side is None else self._macro_side.name,
            "local_side": "NEUTRAL" if self._local_side is None else self._local_side.name,
            "macro_reference_high": self._pivot_record(self._macro_reference_high),
            "macro_reference_low": self._pivot_record(self._macro_reference_low),
            "local_reference_high": self._pivot_record(self._local_reference_high),
            "local_reference_low": self._pivot_record(self._local_reference_low),
            "counts": dict(sorted(self._state_counts.items())),
            "rule_provenance": CAUSAL_STRUCTURE_STATE_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1StateBundle
