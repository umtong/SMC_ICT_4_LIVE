"""Ordered 60m→15m displacement continuation over the robust complete router.

The failed account treated every 60-minute close break as an indefinitely valid
trend label.  A real continuation trade in the supplied material is an active
expansion sequence: a higher-frame displacement establishes direction, a later
same-side decision-frame displacement resumes it, and only then may the first
pullback/retest execute.

This router therefore requires:

1. the latest 60m BOS candle creates a same-side high-quality OB or FVG;
2. a later/equal 15m structure break creates the same evidence and side;
3. the candidate interaction itself begins no earlier than that 15m event.

A later 60m or 15m break without displacement clears that frame's permission.
No elapsed-time parameter is used.  Major-liquidity sweep/reclaim remains an
independent reversal mechanism; everything else must belong to the ordered
expansion episode.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle, Side
from easychart_re1_decision_area_v2 import EasyChartRE1DecisionAreaV2Bundle
from easychart_zones import PriceZone, ZoneKind, ZoneSide


ORDERED_DISPLACEMENT_RULE = (
    "SOURCE_EXPLICIT:"
    "CONTINUATION_REQUIRES_HIGH_QUALITY_SAME_SIDE_DISPLACEMENT_ON_THE_CURRENT_SIXTY_MINUTE_BOS_THEN_A_FIFTEEN_MINUTE_BREAK_BEFORE_THE_TRADED_INTERACTION"
)
if ORDERED_DISPLACEMENT_RULE not in _contracts.SOURCE_RULES:
    _contracts.SOURCE_RULES += (ORDERED_DISPLACEMENT_RULE,)


class EasyChartRE1DisplacementV2Bundle(EasyChartRE1DecisionAreaV2Bundle):
    """Complete natural mechanisms with ordered multi-timeframe expansion."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._seen_macro_displacement_pivot_id: str | None = None
        self._seen_local_displacement_time_ns: int | None = None
        self._macro_displacement_side: Side | None = None
        self._macro_displacement_time_ns: int | None = None
        self._local_displacement_side: Side | None = None
        self._local_displacement_time_ns: int | None = None
        self._ordered_displacement_counts: dict[str, int] = {}

    def _displacement_inc(self, key: str) -> None:
        self._ordered_displacement_counts[key] = self._ordered_displacement_counts.get(key, 0) + 1

    @staticmethod
    def _qualified(zone: PriceZone) -> bool:
        return zone.kind is ZoneKind.FVG or bool(
            zone.kind is ZoneKind.ORDER_BLOCK
            and getattr(zone, "high_quality_by_size", False)
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

    def _refresh_macro(self, bar: Candle) -> None:
        pivot = self._last_direction_pivot
        side = self._macro_side
        if pivot is None or side is None or pivot.pivot_id == self._seen_macro_displacement_pivot_id:
            return
        self._seen_macro_displacement_pivot_id = pivot.pivot_id
        if self._event_has_displacement(self.macro_footprints, bar, side):
            self._macro_displacement_side = side
            self._macro_displacement_time_ns = bar.ts_close_ns
            self._displacement_inc("macro_displacement_confirmed")
            kind = "macro_displacement_confirmed"
        else:
            self._macro_displacement_side = None
            self._macro_displacement_time_ns = None
            self._local_displacement_side = None
            self._local_displacement_time_ns = None
            self._displacement_inc("macro_break_without_displacement_cleared")
            kind = "macro_break_without_displacement_cleared"
        self._bundle_trace.append(
            {
                "scenario_kind": kind,
                "event_time_ns": bar.ts_close_ns,
                "symbol": self.symbol,
                "side": side.name,
                "pivot_id": pivot.pivot_id,
                "rule_provenance": ORDERED_DISPLACEMENT_RULE,
            },
        )

    def _refresh_local(self, bar: Candle) -> None:
        side = self._local_side
        break_time = self._local_break_time_ns
        if side is None or break_time is None or break_time != bar.ts_close_ns:
            return
        if break_time == self._seen_local_displacement_time_ns:
            return
        self._seen_local_displacement_time_ns = break_time
        macro_time = self._macro_displacement_time_ns
        ordered = bool(
            self._macro_displacement_side is side
            and macro_time is not None
            and break_time >= macro_time
        )
        if ordered and self._event_has_displacement(self.decision_footprints, bar, side):
            self._local_displacement_side = side
            self._local_displacement_time_ns = break_time
            self._displacement_inc("ordered_local_displacement_confirmed")
            kind = "ordered_local_displacement_confirmed"
        else:
            self._local_displacement_side = None
            self._local_displacement_time_ns = None
            self._displacement_inc("local_break_without_ordered_displacement_cleared")
            kind = "local_break_without_ordered_displacement_cleared"
        self._bundle_trace.append(
            {
                "scenario_kind": kind,
                "event_time_ns": bar.ts_close_ns,
                "symbol": self.symbol,
                "side": side.name,
                "macro_side": (
                    None if self._macro_displacement_side is None else self._macro_displacement_side.name
                ),
                "macro_time_ns": macro_time,
                "rule_provenance": ORDERED_DISPLACEMENT_RULE,
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
            and self._local_displacement_time_ns >= self._macro_displacement_time_ns
            and plan.interaction_time_ns >= self._local_displacement_time_ns
            and plan.observed_time_ns >= self._local_displacement_time_ns
        )
        if valid:
            self._displacement_inc("plan_allowed_ordered_displacement")
            return True
        self._displacement_inc("plan_deferred_outside_ordered_displacement")
        self._bundle_trace.append(
            {
                "scenario_kind": "plan_deferred_outside_ordered_displacement",
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "side": plan.side.name,
                "interaction_time_ns": plan.interaction_time_ns,
                "macro_displacement_side": (
                    None if self._macro_displacement_side is None else self._macro_displacement_side.name
                ),
                "macro_displacement_time_ns": self._macro_displacement_time_ns,
                "local_displacement_side": (
                    None if self._local_displacement_side is None else self._local_displacement_side.name
                ),
                "local_displacement_time_ns": self._local_displacement_time_ns,
                "rule_provenance": ORDERED_DISPLACEMENT_RULE,
            },
        )
        return False

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        plans = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes == self.CONTEXT_MINUTES:
            self._refresh_macro(bar)
        elif timeframe_minutes == 15:
            self._refresh_local(bar)
        return plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["ordered_displacement_router"] = {
            "counts": dict(sorted(self._ordered_displacement_counts.items())),
            "macro_side": (
                None if self._macro_displacement_side is None else self._macro_displacement_side.name
            ),
            "macro_time_ns": self._macro_displacement_time_ns,
            "local_side": (
                None if self._local_displacement_side is None else self._local_displacement_side.name
            ),
            "local_time_ns": self._local_displacement_time_ns,
            "rule_provenance": ORDERED_DISPLACEMENT_RULE,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1DisplacementV2Bundle
