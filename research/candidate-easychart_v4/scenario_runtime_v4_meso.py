"""Add a source-supported 5m-structure -> 1m-entry scenario scale.

EasyChart lists 1h, 15m and 5m as day-trading chart scales and repeatedly uses a
larger structure with a lower-timeframe OB/FVG entry.  The existing v4 system
implemented 60m->5m and 15m->1m, but no 5m->1m family.  A human trader naturally
zooms from a live 1h scene into both 15m and 5m structures before executing on
1m.

This module reuses the identical source-faithful state machine at 5m->1m.  It
adds no loosened threshold.  Every MESO plan still requires the same live,
aligned 1h structural event, source-sequenced Fakeout/Trap or accepted-break
retest, event-local high-quality OB/FVG, and first later footprint retest.

If 15m and 5m interpret the same side at the same interaction close, the 15m
interpretation wins deterministically.  Different 5m structural events remain
separate causal episodes; the one global account and active position still
arbitrate actual submissions.
"""
from __future__ import annotations

from typing import Any

from domain import Candle, Side
from easychart_mtf_scenario import MTFTradePlan
from easychart_zones import PriceZone
from market_structure import StructuralBoundary
from scenario_bundle_v4 import (
    StructuralSetup,
    _EvidenceDetectorView,
)
from scenario_runtime_v4_acceptance_gate import (
    SourceFaithfulRetestEntryGatedBundle,
    SourceFaithfulRetestEntryGatedEngine,
)


class MesoResearchBundle(SourceFaithfulRetestEntryGatedBundle):
    """One 1h scene with 15m->1m and 5m->1m execution families."""

    MESO_SOURCE_RULE = "SOURCE_EXPLICIT:DAY_TRADING_USES_1H_15M_AND_5M_CHARTS"
    MESO_TRANSLATION_RULES = (
        "HUMAN_NATURAL_INFERENCE:LIVE_1H_CONTEXT_MAY_BE_REFINED_BY_A_CAUSALLY_LATER_5M_STRUCTURE",
        "HUMAN_NATURAL_INFERENCE:SAME_CLOSE_15M_STRUCTURE_PRECEDES_5M_STRUCTURE",
    )

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.meso = SourceFaithfulRetestEntryGatedEngine(
            symbol,
            tick_size,
            scale_name="MESO",
            context_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.detectors = _EvidenceDetectorView(
            {
                60: self.macro.structure,
                15: self.micro.structure,
                5: self.meso.structure,
            },
            (
                self.macro.trigger_detector,
                self.micro.trigger_detector,
                self.meso.trigger_detector,
            ),
        )

    @property
    def setups(self) -> list[StructuralSetup]:
        return self.macro.setups + self.micro.setups + self.meso.setups

    @property
    def plans(self) -> list[MTFTradePlan]:
        return self.macro.plans + self.micro.plans + self.meso.plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "macro": self.macro.diagnostics,
            "micro": self.micro.diagnostics,
            "meso": self.meso.diagnostics,
            "macro_structure": self.macro.structure.diagnostics,
            "micro_structure": self.micro.structure.diagnostics,
            "meso_structure": self.meso.structure.diagnostics,
            "top_down_router": dict(self._routing_diagnostics),
        }

    def _route_meso_plans(self, plans: list[MTFTradePlan]) -> list[MTFTradePlan]:
        accepted = self._route_micro_plans(plans)
        output: list[MTFTradePlan] = []
        for plan in accepted:
            # The inherited route already appends the 1h provenance.  Add only
            # the source-supported intermediate-scale provenance.
            output.append(
                type(plan)(
                    **{
                        **{
                            field: getattr(plan, field)
                            for field in plan.__dataclass_fields__
                        },
                        "source_rule_count": plan.source_rule_count + 1,
                        "rule_provenance": (
                            plan.rule_provenance
                            + (self.MESO_SOURCE_RULE,)
                            + self.MESO_TRANSLATION_RULES
                        ),
                    },
                ),
            )
        return output

    def _deduplicate(self, plans: list[MTFTradePlan]) -> list[MTFTradePlan]:
        ranked = sorted(
            plans,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.observed_time_ns,
                plan.plan_id,
            ),
        )
        independent: list[MTFTradePlan] = []
        for plan in ranked:
            episode = (plan.side, plan.interaction_time_ns)
            if episode in self._claimed_episodes:
                self._bundle_trace.append(
                    {
                        "scenario_kind": "causal_episode_duplicate_suppressed",
                        "event_time_ns": plan.observed_time_ns,
                        "scale_name": plan.scale_name,
                        "higher_timeframe_minutes": plan.higher_timeframe_minutes,
                        "decision_timeframe_minutes": plan.decision_timeframe_minutes,
                        "trigger_timeframe_minutes": plan.trigger_timeframe_minutes,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "interaction_time_ns": plan.interaction_time_ns,
                    },
                )
                continue
            self._claimed_episodes.add(episode)
            independent.append(plan)
        return independent

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        plans: list[MTFTradePlan] = []
        if timeframe_minutes in (60, 5):
            plans.extend(self.macro.on_bar(timeframe_minutes, bar))
            if timeframe_minutes == 60:
                self._record_context_change(bar.ts_close_ns)
        if timeframe_minutes in (15, 1):
            plans.extend(
                self._route_micro_plans(
                    self.micro.on_bar(timeframe_minutes, bar),
                ),
            )
        if timeframe_minutes in (5, 1):
            plans.extend(
                self._route_meso_plans(
                    self.meso.on_bar(timeframe_minutes, bar),
                ),
            )
        return self._deduplicate(plans)

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            self.macro.drain_trace()
            + self.micro.drain_trace()
            + self.meso.drain_trace()
            + self._bundle_trace
        )
        self._bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> StructuralBoundary | PriceZone | None:
        return (
            self.macro.find_zone(zone_id)
            or self.micro.find_zone(zone_id)
            or self.meso.find_zone(zone_id)
        )


__all__ = ["MesoResearchBundle"]
