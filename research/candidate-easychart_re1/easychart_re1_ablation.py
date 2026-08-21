"""Structural ablations for the failed persistent-continuation generalization.

These are not parameter searches.  They isolate two causal questions before the
next system is chosen:

1. Is most of the failure caused by weak execution locations and remote targets?
2. After fixing those, is a current 15-minute structure break sufficient, or is
   the complete 60m impulse -> pullback -> resumption sequence necessary?

All variants share exactly the same entry/stop/target construction, costs,
position sizing and post-entry full-stop management.  Only the router differs.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import V5TradePlan
from domain import Candle, Side
from easychart_re1_impulse import (
    ImpulseHorizontalScenarioEngine,
    ImpulseNaturalScenarioEngine,
)
from easychart_re1_phase import EasyChartRE1PhaseBundle


CURRENT_LOCAL_STRUCTURE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "CONTINUATION_REQUIRES_THE_MOST_RECENT_CONFIRMED_FIFTEEN_MINUTE_STRUCTURE_BREAK_TO_MATCH_THE_TRADE_SIDE"
)
if CURRENT_LOCAL_STRUCTURE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CURRENT_LOCAL_STRUCTURE_RULE,)


class EasyChartRE1LocationBundle(EasyChartRE1PhaseBundle):
    """Persistent 60m router with only location/response/objective repairs."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = ImpulseNaturalScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal = ImpulseHorizontalScenarioEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0
        self._audit_offsets["horizontal"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["natural_objectives"] = {
            "micro": self.micro.first_obstacle_diagnostics,
            "horizontal": self.horizontal.first_obstacle_diagnostics,
        }
        output["ablation"] = "LOCATION_RESPONSE_OBJECTIVE_ONLY"
        return output


class EasyChartRE1LocalAlignmentBundle(EasyChartRE1LocationBundle):
    """Location repairs plus the current 15m BOS side as an execution router."""

    LOCAL_DIRECTION_PIVOT_SPAN = 2

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._broken_local_pivot_ids: set[str] = set()
        self._local_side: Side | None = None
        self._local_break_time_ns: int | None = None
        self._local_break_pivot_id: str | None = None
        self._local_counts: dict[str, int] = {}

    def _local_inc(self, key: str) -> None:
        self._local_counts[key] = self._local_counts.get(key, 0) + 1

    def _advance_local_direction(self, bar: Candle) -> None:
        breaks = []
        for pivot in self.micro.structure.pivots:
            if (
                pivot.span != self.LOCAL_DIRECTION_PIVOT_SPAN
                or pivot.pivot_id in self._broken_local_pivot_ids
                or pivot.observed_time_ns >= bar.ts_close_ns
            ):
                continue
            side = None
            if pivot.side == "HIGH" and bar.close > pivot.price:
                side = Side.LONG
            elif pivot.side == "LOW" and bar.close < pivot.price:
                side = Side.SHORT
            if side is None:
                continue
            self._broken_local_pivot_ids.add(pivot.pivot_id)
            breaks.append((side, pivot))
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
        changed = side is not self._local_side
        self._local_side = side
        self._local_break_time_ns = bar.ts_close_ns
        self._local_break_pivot_id = pivot.pivot_id
        self._local_inc("local_break_events")
        if changed:
            self._local_inc("local_direction_changes")
        self._bundle_trace.append(
            {
                "scenario_kind": "current_local_structure_break",
                "event_time_ns": bar.ts_close_ns,
                "symbol": self.symbol,
                "side": side.name,
                "pivot_id": pivot.pivot_id,
                "pivot_price": pivot.price,
                "direction_changed": changed,
                "rule_provenance": CURRENT_LOCAL_STRUCTURE_RULE,
            },
        )

    def _route_plan(self, plan: V5TradePlan) -> bool:
        if not super()._route_plan(plan):
            return False
        allowed = bool(
            self._local_side is plan.side
            and self._local_break_time_ns is not None
            and plan.observed_time_ns >= self._local_break_time_ns
        )
        if allowed:
            self._local_inc("plan_allowed_current_local_alignment")
            return True
        self._local_inc("plan_deferred_against_current_local_structure")
        self._bundle_trace.append(
            {
                "scenario_kind": "plan_deferred_against_current_local_structure",
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "side": plan.side.name,
                "local_side": None if self._local_side is None else self._local_side.name,
                "local_break_time_ns": self._local_break_time_ns,
                "rule_provenance": CURRENT_LOCAL_STRUCTURE_RULE,
            },
        )
        return False

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        plans = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes == 15:
            self._advance_local_direction(bar)
        return plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["current_local_structure_router"] = {
            "side": None if self._local_side is None else self._local_side.name,
            "break_time_ns": self._local_break_time_ns,
            "break_pivot_id": self._local_break_pivot_id,
            "counts": dict(sorted(self._local_counts.items())),
            "rule_provenance": CURRENT_LOCAL_STRUCTURE_RULE,
        }
        output["ablation"] = "LOCATION_RESPONSE_OBJECTIVE_PLUS_CURRENT_15M_ALIGNMENT"
        return output


__all__ = [
    "EasyChartRE1LocationBundle",
    "EasyChartRE1LocalAlignmentBundle",
]
