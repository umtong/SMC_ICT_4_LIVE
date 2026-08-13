"""Displacement-qualified continuation for the complete EasyChart mechanisms.

A close through a pivot is not automatically an impulse.  The source material
uses a strong engulfing OB or FVG as evidence that one side actually displaced
the auction.  The failed random intervals treated every 60m/15m close break as
continuation, which left the router permanently bullish or bearish through late
trend, pullback and range states.

This policy retains the natural geometry and independent major-liquidity
reversal family, but continuation/bounce plans require both:

* the current 60-minute BOS event produced a same-side high-quality OB or FVG;
* the latest same-side 15-minute structure break produced the same evidence.

The evidence must be created on the completed break candle itself.  It is a
causal state transition, not a volatility score or fitted magnitude threshold.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle, Side
from easychart_re1_decision_area import EasyChartRE1DecisionAreaBundle
from easychart_zones import PriceZone, ZoneKind, ZoneSide


DISPLACEMENT_CONTINUATION_RULE = (
    "SOURCE_EXPLICIT:"
    "CONTINUATION_REQUIRES_SAME_SIDE_HIGH_QUALITY_OB_OR_FVG_ON_BOTH_THE_CURRENT_SIXTY_MINUTE_AND_FIFTEEN_MINUTE_STRUCTURE_BREAK_EVENTS"
)
if DISPLACEMENT_CONTINUATION_RULE not in _contracts.SOURCE_RULES:
    _contracts.SOURCE_RULES += (DISPLACEMENT_CONTINUATION_RULE,)


class EasyChartRE1DisplacementBundle(EasyChartRE1DecisionAreaBundle):
    """Decision-area system whose continuation router requires real displacement."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._seen_macro_break_for_displacement: str | None = None
        self._macro_displacement_side: Side | None = None
        self._macro_displacement_time_ns: int | None = None
        self._local_displacement_side: Side | None = None
        self._local_displacement_time_ns: int | None = None
        self._displacement_counts: dict[str, int] = {}

    def _displacement_inc(self, key: str) -> None:
        self._displacement_counts[key] = self._displacement_counts.get(key, 0) + 1

    @staticmethod
    def _qualified(zone: PriceZone) -> bool:
        return zone.kind is ZoneKind.FVG or (
            zone.kind is ZoneKind.ORDER_BLOCK and zone.high_quality_by_size
        )

    @staticmethod
    def _wanted(side: Side) -> ZoneSide:
        return ZoneSide.SUPPORT if side is Side.LONG else ZoneSide.RESISTANCE

    def _event_has_displacement(self, detector: Any, bar: Candle, side: Side) -> bool:
        wanted = self._wanted(side)
        return any(
            zone.side is wanted
            and zone.observed_time_ns == bar.ts_close_ns
            and self._qualified(zone)
            for zone in detector.active_zones(side=wanted)
        )

    def _refresh_macro_displacement(self, bar: Candle) -> None:
        pivot = self._last_direction_pivot
        side = self._macro_side
        if pivot is None or side is None or pivot.pivot_id == self._seen_macro_break_for_displacement:
            return
        self._seen_macro_break_for_displacement = pivot.pivot_id
        if self._event_has_displacement(self.macro_footprints, bar, side):
            self._macro_displacement_side = side
            self._macro_displacement_time_ns = bar.ts_close_ns
            self._displacement_inc("macro_displacement_confirmed")
            kind = "macro_displacement_confirmed"
        else:
            self._macro_displacement_side = None
            self._macro_displacement_time_ns = None
            self._displacement_inc("macro_break_without_displacement")
            kind = "macro_break_without_displacement"
        self._bundle_trace.append(
            {
                "scenario_kind": kind,
                "event_time_ns": bar.ts_close_ns,
                "symbol": self.symbol,
                "side": side.name,
                "pivot_id": pivot.pivot_id,
                "rule_provenance": DISPLACEMENT_CONTINUATION_RULE,
            },
        )

    def _refresh_local_displacement(self, bar: Candle) -> None:
        side = self._local_side
        if side is None or self._local_break_time_ns != bar.ts_close_ns:
            return
        if self._event_has_displacement(self.decision_footprints, bar, side):
            self._local_displacement_side = side
            self._local_displacement_time_ns = bar.ts_close_ns
            self._displacement_inc("local_displacement_confirmed")
            kind = "local_displacement_confirmed"
        else:
            self._local_displacement_side = None
            self._local_displacement_time_ns = None
            self._displacement_inc("local_break_without_displacement")
            kind = "local_break_without_displacement"
        self._bundle_trace.append(
            {
                "scenario_kind": kind,
                "event_time_ns": bar.ts_close_ns,
                "symbol": self.symbol,
                "side": side.name,
                "rule_provenance": DISPLACEMENT_CONTINUATION_RULE,
            },
        )

    def _route_plan(self, plan: V5TradePlan) -> bool:
        if not super()._route_plan(plan):
            return False
        valid = bool(
            self._macro_displacement_side is plan.side
            and self._local_displacement_side is plan.side
            and self._macro_displacement_time_ns is not None
            and self._local_displacement_time_ns is not None
            and plan.observed_time_ns >= self._local_displacement_time_ns
        )
        if valid:
            self._displacement_inc("plan_allowed_displacement_continuation")
            return True
        self._displacement_inc("plan_deferred_without_current_displacement")
        self._bundle_trace.append(
            {
                "scenario_kind": "plan_deferred_without_current_displacement",
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "side": plan.side.name,
                "macro_displacement_side": (
                    None if self._macro_displacement_side is None else self._macro_displacement_side.name
                ),
                "local_displacement_side": (
                    None if self._local_displacement_side is None else self._local_displacement_side.name
                ),
                "rule_provenance": DISPLACEMENT_CONTINUATION_RULE,
            },
        )
        return False

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        plans = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes == self.CONTEXT_MINUTES:
            self._refresh_macro_displacement(bar)
        elif timeframe_minutes == 15:
            self._refresh_local_displacement(bar)
        return plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["displacement_router"] = {
            "counts": dict(sorted(self._displacement_counts.items())),
            "macro_side": (
                None if self._macro_displacement_side is None else self._macro_displacement_side.name
            ),
            "macro_time_ns": self._macro_displacement_time_ns,
            "local_side": (
                None if self._local_displacement_side is None else self._local_displacement_side.name
            ),
            "local_time_ns": self._local_displacement_time_ns,
            "rule_provenance": DISPLACEMENT_CONTINUATION_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DisplacementBundle
