"""EasyChart RE1: top-down context routing for the v20 diagonal execution core.

The v20 candidate already encoded the lower-timeframe EasyChart sequence well:
pre-existing trend-line/channel structure -> rejection or accepted break ->
first distinct retest -> one immutable entry/stop/target plan.  Its unresolved
problem was direction.  The 60-minute frame was recorded but never participated
in the decision, so the same local pattern could be traded both with and against
the larger auction.

RE1 gives the 60-minute chart the role shown in the supplied EasyChart material
and trading cases:

* a causal close-confirmed break of a previously confirmed 60-minute wick swing
  supplies the current structural direction;
* local 15/5/1 plans which agree with that direction are continuation plans;
* before a 60-minute break has established direction, local plans remain valid
  range/transition opportunities;
* a plan against the current direction is allowed only at a pre-existing
  same-side 60-minute decision area (structure, OB, or FVG).  The local v20
  reclaim/retest remains the entry confirmation, so the higher-timeframe area
  is context rather than an unconditional signal.

No score, volatility threshold, clock filter, risk multiplier, trade-count
limit, or post-entry management rule is introduced.  NautilusTrader remains the
single authority for orders, fills, fees, positions and continuous NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from causal_lifecycle_v5 import LifecycleAwareStructureBook
from contracts_v5 import Pivot, StructureZone, V5TradePlan
from diagonal_core_v20 import MicroDiagonalCoreBundleV20
from domain import Candle, Side
from easychart_zones import EasyChartZoneDetector, ZoneSide


HTF_DIRECTION_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "SIXTY_MINUTE_CLOSE_BREAK_OF_CONFIRMED_WICK_SWING_ROUTES_LOCAL_DIRECTION"
)
HTF_REVERSAL_AREA_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "COUNTERTREND_LOCAL_PLAN_REQUIRES_PREEXISTING_SAME_SIDE_SIXTY_MINUTE_DECISION_AREA"
)
HTF_NEUTRAL_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "NO_CONFIRMED_SIXTY_MINUTE_BREAK_MEANS_RANGE_OR_TRANSITION_NOT_FORCED_DIRECTION"
)
for _rule in (HTF_DIRECTION_RULE, HTF_REVERSAL_AREA_RULE, HTF_NEUTRAL_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class ContextEvidence:
    evidence_id: str
    evidence_kind: str
    lower: float
    upper: float
    observed_time_ns: int
    source: str


class EasyChartRE1Bundle(MicroDiagonalCoreBundleV20):
    """Micro execution core routed by causal 60-minute market context."""

    CONTEXT_MINUTES = 60
    DIRECTION_PIVOT_SPAN = 2

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.macro_structure = LifecycleAwareStructureBook(
            symbol,
            self.CONTEXT_MINUTES,
            tick_size,
            pivot_spans=(2, 6),
        )
        self.macro_footprints = EasyChartZoneDetector(
            symbol,
            self.CONTEXT_MINUTES,
            tick_size,
        )
        # The supplied live examples repeatedly use 15m/5m OB overlap for the
        # exact entry area.  RE1 records 15m footprints for diagnosis and
        # provenance, but does not turn their presence into another gate.
        self.decision_footprints = EasyChartZoneDetector(symbol, 15, tick_size)

        self._macro_side: Side | None = None
        self._last_direction_pivot: Pivot | None = None
        self._broken_direction_pivot_ids: set[str] = set()
        self._recent_macro_interactions: list[StructureZone] = []
        self._router_counts: dict[str, int] = {}

    def _router_inc(self, key: str) -> None:
        self._router_counts[key] = self._router_counts.get(key, 0) + 1

    @staticmethod
    def _kind_value(kind: Any) -> str:
        return str(getattr(kind, "value", kind))

    @staticmethod
    def _zone_side_for_plan(plan: V5TradePlan) -> ZoneSide:
        return ZoneSide.SUPPORT if plan.side is Side.LONG else ZoneSide.RESISTANCE

    @staticmethod
    def _bar_touches(bar: Candle, lower: float, upper: float) -> bool:
        return bar.low <= upper and bar.high >= lower

    def _interval_touches_plan(self, lower: float, upper: float, plan: V5TradePlan) -> bool:
        band_overlap = (
            max(lower, plan.overlap_lower)
            <= min(upper, plan.overlap_upper) + self.tick_size
        )
        entry_inside = lower - self.tick_size <= plan.entry <= upper + self.tick_size
        return band_overlap or entry_inside

    def _register_audit_zone(self, timeframe: int, zone: Any) -> None:
        detector = self.detectors.get(timeframe)
        if detector is not None:
            detector.register(zone)

    def _newly_broken_direction_pivots(self, bar: Candle) -> list[tuple[Side, Pivot]]:
        output: list[tuple[Side, Pivot]] = []
        for pivot in self.macro_structure.pivots:
            if pivot.span != self.DIRECTION_PIVOT_SPAN:
                continue
            if pivot.pivot_id in self._broken_direction_pivot_ids:
                continue
            # A pivot becomes usable only after the right-side confirmation bars
            # have closed.  A break on that same observation timestamp is not
            # allowed to retroactively use it.
            if pivot.observed_time_ns >= bar.ts_close_ns:
                continue
            side: Side | None = None
            if pivot.side == "HIGH" and bar.close > pivot.price:
                side = Side.LONG
            elif pivot.side == "LOW" and bar.close < pivot.price:
                side = Side.SHORT
            if side is None:
                continue
            self._broken_direction_pivot_ids.add(pivot.pivot_id)
            output.append((side, pivot))
        return output

    def _advance_macro_direction(self, bar: Candle) -> None:
        breaks = self._newly_broken_direction_pivots(bar)
        if not breaks:
            return
        # A single close can clear several nested old pivots.  The most recent
        # causal swing is the structure decision; span is fixed above and never
        # selected from PnL.
        side, pivot = max(
            breaks,
            key=lambda item: (
                item[1].event_time_ns,
                item[1].observed_time_ns,
                item[1].pivot_id,
            ),
        )
        changed = side is not self._macro_side
        self._macro_side = side
        self._last_direction_pivot = pivot
        self._router_inc("htf_break_events")
        if changed:
            self._router_inc("htf_direction_changes")
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
                "rule_provenance": HTF_DIRECTION_RULE,
            },
        )

    def _update_macro_context(self, bar: Candle) -> None:
        self.macro_structure.on_bar(bar)
        created = self.macro_footprints.on_bar(bar)
        for zone in created:
            self._register_audit_zone(self.CONTEXT_MINUTES, zone)

        self._advance_macro_direction(bar)

        # Preserve the structures actually interacted with by this completed
        # HTF bar for the ensuing retest episode before the lifecycle book
        # retires their projected boundary.
        touched: list[StructureZone] = []
        for zone in self.macro_structure.boundaries_at(bar.ts_close_ns):
            if self._bar_touches(bar, zone.lower, zone.upper):
                touched.append(zone)
                self._register_audit_zone(self.CONTEXT_MINUTES, zone)
        self._recent_macro_interactions = touched
        if touched:
            self._router_inc("htf_structure_interaction_bars")
        self.macro_structure.observe_price(bar)

    def _update_decision_footprints(self, bar: Candle) -> None:
        created = self.decision_footprints.on_bar(bar)
        for zone in created:
            self._register_audit_zone(15, zone)

    def _structure_evidence(self, plan: V5TradePlan) -> list[ContextEvidence]:
        wanted = self._zone_side_for_plan(plan)
        zones: list[StructureZone] = []
        zones.extend(self.macro_structure.boundaries_at(plan.observed_time_ns))
        zones.extend(self._recent_macro_interactions)

        output: list[ContextEvidence] = []
        seen: set[str] = set()
        for zone in zones:
            if zone.side is not wanted:
                continue
            if zone.observed_time_ns > plan.observed_time_ns:
                continue
            if not self._interval_touches_plan(zone.lower, zone.upper, plan):
                continue
            source_id = zone.source_structure_id
            if source_id in seen:
                continue
            seen.add(source_id)
            output.append(
                ContextEvidence(
                    evidence_id=source_id,
                    evidence_kind=self._kind_value(zone.kind),
                    lower=zone.lower,
                    upper=zone.upper,
                    observed_time_ns=zone.observed_time_ns,
                    source="HTF_STRUCTURE",
                ),
            )
        return output

    def _footprint_evidence(self, plan: V5TradePlan) -> list[ContextEvidence]:
        wanted = self._zone_side_for_plan(plan)
        output: list[ContextEvidence] = []
        for zone in self.macro_footprints.active_zones(side=wanted):
            if zone.observed_time_ns > plan.observed_time_ns:
                continue
            if not self._interval_touches_plan(zone.lower, zone.upper, plan):
                continue
            output.append(
                ContextEvidence(
                    evidence_id=zone.zone_id,
                    evidence_kind=self._kind_value(zone.kind),
                    lower=zone.lower,
                    upper=zone.upper,
                    observed_time_ns=zone.observed_time_ns,
                    source="HTF_FOOTPRINT",
                ),
            )
        return output

    def _decision_footprint_ids(self, plan: V5TradePlan) -> list[str]:
        wanted = self._zone_side_for_plan(plan)
        return [
            zone.zone_id
            for zone in self.decision_footprints.active_zones(side=wanted)
            if zone.observed_time_ns <= plan.observed_time_ns
            and self._interval_touches_plan(zone.lower, zone.upper, plan)
        ]

    def _route_plan(self, plan: V5TradePlan) -> bool:
        macro_side = self._macro_side
        macro_side_name = "NEUTRAL" if macro_side is None else macro_side.name
        evidence = self._structure_evidence(plan) + self._footprint_evidence(plan)
        decision_footprints = self._decision_footprint_ids(plan)

        if macro_side is None:
            allowed = True
            reason = "context_router_allowed_neutral_htf"
            provenance = HTF_NEUTRAL_RULE
        elif plan.side is macro_side:
            allowed = True
            reason = "context_router_allowed_continuation"
            provenance = HTF_DIRECTION_RULE
        elif evidence:
            allowed = True
            reason = "context_router_allowed_htf_reversal_area"
            provenance = HTF_REVERSAL_AREA_RULE
        else:
            allowed = False
            reason = "context_router_rejected_against_htf_direction"
            provenance = HTF_REVERSAL_AREA_RULE

        self._router_inc(reason)
        self._bundle_trace.append(
            {
                "scenario_kind": reason,
                "event_time_ns": plan.observed_time_ns,
                "symbol": plan.symbol,
                "plan_id": plan.plan_id,
                "setup_id": plan.setup_id,
                "side": plan.side.name,
                "macro_side": macro_side_name,
                "macro_break_pivot_id": (
                    None if self._last_direction_pivot is None else self._last_direction_pivot.pivot_id
                ),
                "macro_evidence": [
                    {
                        "id": item.evidence_id,
                        "kind": item.evidence_kind,
                        "source": item.source,
                        "lower": item.lower,
                        "upper": item.upper,
                        "observed_time_ns": item.observed_time_ns,
                    }
                    for item in evidence
                ],
                "decision_footprint_ids": decision_footprints,
                "scenario_path": plan.scenario_path,
                "interaction_time_ns": plan.interaction_time_ns,
                "entry": plan.entry,
                "stop": plan.stop,
                "target": plan.target,
                "gross_rr": plan.gross_rr,
                "rule_provenance": provenance,
            },
        )
        return allowed

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == self.CONTEXT_MINUTES:
            self._update_macro_context(bar)
        elif timeframe_minutes == 15:
            self._update_decision_footprints(bar)

        plans = super().on_bar(timeframe_minutes, bar)
        if not plans:
            return []
        return [plan for plan in plans if self._route_plan(plan)]

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["top_down_context_router"] = {
            "policy": (
                "60M confirmed-wick BOS direction; continuation allowed; neutral allowed; "
                "countertrend only at same-side 60M structure/OB/FVG"
            ),
            "current_side": "NEUTRAL" if self._macro_side is None else self._macro_side.name,
            "direction_pivot_span": self.DIRECTION_PIVOT_SPAN,
            "last_direction_pivot_id": (
                None if self._last_direction_pivot is None else self._last_direction_pivot.pivot_id
            ),
            "counts": dict(sorted(self._router_counts.items())),
            "macro_structure": dict(self.macro_structure.diagnostics),
            "macro_footprints": dict(self.macro_footprints.diagnostics),
            "decision_footprints": dict(self.decision_footprints.diagnostics),
            "rules": (
                HTF_DIRECTION_RULE,
                HTF_REVERSAL_AREA_RULE,
                HTF_NEUTRAL_RULE,
            ),
        }
        return output


# Compatibility name for runners which patch mtf_strategy.MultiScaleScenarioBundle.
MultiScaleScenarioBundle = EasyChartRE1Bundle
