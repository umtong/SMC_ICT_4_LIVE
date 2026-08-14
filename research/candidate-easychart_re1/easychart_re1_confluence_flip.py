"""Multi-structure S/R-flip acceptance for the EasyChart RE1 account.

The supplied live case does not buy a generic trend-line break.  Three already
visible resistances -- a prior horizontal level, a channel boundary and a
separate descending line -- are cleared by one decision event, hold as support,
and are bought only when the lower frame confirms the return.  This module
assigns that complete scenario one responsibility:

* generic isolated diagonal acceptance is retired;
* a 5-minute body break may arm acceptance only when the same completed bar
  clears one connected price cluster containing at least two genuinely distinct
  15-minute structure facts from at least two structure families;
* the next 5-minute bar must hold outside the full cluster;
* the first exact return uses a strong event-local engulfing OB immediately,
  an ordinary OB/FVG response, or causal retest flow;
* stop, objective, account routing, costs and fixed risk remain inherited.

There is no distance, ATR, session, score or fitted-count threshold.  Structures
belong together only when their projected price bands overlap or touch by one
tradable tick, and the breakout bar itself trades through the cluster.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import (
    ScenarioPath,
    SetupState,
    StructureFamily,
    StructureZone,
    V5TradePlan,
)
from domain import Candle, Side
from easychart_re1_flow_ob_sweep_responsibility import (
    EasyChartRE1ResponsibleSweepFlowOBBundle,
    VisualFootprintOwnsCurrentBarFlowMixin,
)
from easychart_re1_human_policy import HumanMicroEngine
from easychart_re1_phase import ChannelPhaseStructureBook
from easychart_zones import ZoneSide


CONFLUENCE_FLIP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_BODY_BREAK_AND_NEXT_BAR_HOLD_OF_MULTIPLE_OVERLAPPING_DISTINCT_STRUCTURES_CREATES_ONE_SR_FLIP_RETEST_SCENARIO"
)
ISOLATED_ACCEPTANCE_RESPONSIBILITY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "ISOLATED_DIAGONAL_ACCEPTANCE_IS_DEFERRED_TO_MULTI_STRUCTURE_SR_FLIP_RESPONSIBILITY"
)
for _rule in (CONFLUENCE_FLIP_RULE, ISOLATED_ACCEPTANCE_RESPONSIBILITY_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class ReversalOnlyPhaseFlowMicroEngine(
    VisualFootprintOwnsCurrentBarFlowMixin,
    HumanMicroEngine,
):
    """Keep diagonal rejection/rotation, but never originate isolated acceptance."""

    def _discover_interactions(self, bar: Candle, previous: Candle, index: int) -> None:
        before = len(self.setups)
        super()._discover_interactions(bar, previous, index)
        for setup in self.setups[before:]:
            if setup.path is not ScenarioPath.ACCEPTANCE:
                continue
            self._active.pop(setup.setup_id, None)
            setup.state = SetupState.UNRESOLVED
            setup.terminal_reason = "isolated_diagonal_acceptance_deferred_to_confluence"
            self._inc("isolated_diagonal_acceptance_deferred_to_confluence")
            self._trace(
                "isolated_diagonal_acceptance_deferred_to_confluence",
                bar.ts_close_ns,
                setup,
                rule_provenance=ISOLATED_ACCEPTANCE_RESPONSIBILITY_RULE,
            )


class ConfluenceAcceptanceEngine(
    VisualFootprintOwnsCurrentBarFlowMixin,
    HumanMicroEngine,
):
    """Acceptance-only engine over a connected cluster of distinct structures."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = ChannelPhaseStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )
        self._confluence_counts: dict[str, int] = {}

    def _cinc(self, key: str) -> None:
        self._confluence_counts[key] = self._confluence_counts.get(key, 0) + 1

    def _canonical_fact(self, zone: StructureZone, time_ns: int) -> str:
        # The main channel edge and the trend line from which it was generated
        # are one price fact.  A parallel edge or an independent diagonal is not.
        if zone.family is StructureFamily.TREND_LINE:
            lookup = getattr(self.structure, "_channel_for_main_line", None)
            main_edge = getattr(self.structure, "_main_edge", None)
            if lookup is not None and main_edge is not None:
                channel = lookup(zone.source_structure_id, time_ns)
                if channel is not None:
                    return f"{channel.channel_id}:{main_edge(channel)}"
        return zone.source_structure_id

    def _distinct_members(
        self,
        group: tuple[StructureZone, ...],
        time_ns: int,
    ) -> tuple[StructureZone, ...]:
        by_fact: dict[str, StructureZone] = {}
        for zone in group:
            fact = self._canonical_fact(zone, time_ns)
            incumbent = by_fact.get(fact)
            if incumbent is None or (
                zone.source_pivot_span,
                self._family_priority(zone),
                zone.strength_ratio,
                zone.zone_id,
            ) > (
                incumbent.source_pivot_span,
                self._family_priority(incumbent),
                incumbent.strength_ratio,
                incumbent.zone_id,
            ):
                by_fact[fact] = zone
        return tuple(
            sorted(
                by_fact.values(),
                key=lambda zone: (
                    zone.lower,
                    zone.upper,
                    -zone.source_pivot_span,
                    zone.zone_id,
                ),
            )
        )

    def _candidate_clusters(
        self,
        bar: Candle,
        previous: Candle,
    ) -> list[tuple[Side, tuple[StructureZone, ...], float, float]]:
        available = [
            zone
            for zone in self.structure.boundaries_at(bar.ts_close_ns)
            if zone.observed_time_ns < bar.ts_close_ns
            and zone.source_structure_id not in self._claimed_structures
            and self._touches(bar, zone)
        ]
        output: list[tuple[Side, tuple[StructureZone, ...], float, float]] = []
        for raw in self._cluster(available):
            members = self._distinct_members(raw, bar.ts_close_ns)
            families = {zone.family for zone in members}
            if len(members) < 2 or len(families) < 2:
                self._cinc("touched_cluster_lacked_distinct_multi_family_confluence")
                continue
            lower = min(zone.lower for zone in members)
            upper = max(zone.upper for zone in members)
            if members[0].side is ZoneSide.RESISTANCE:
                broke = (
                    previous.close <= upper
                    and bar.close > upper
                    and bar.close > bar.open
                )
                side = Side.LONG
            else:
                broke = (
                    previous.close >= lower
                    and bar.close < lower
                    and bar.close < bar.open
                )
                side = Side.SHORT
            if not broke:
                self._cinc("multi_family_cluster_touched_without_body_break")
                continue
            output.append((side, members, lower, upper))
        return output

    def _discover_interactions(self, bar: Candle, previous: Candle, index: int) -> None:
        candidates = self._candidate_clusters(bar, previous)
        sides = {item[0] for item in candidates}
        if len(sides) > 1:
            self._cinc("decision_bar_broke_both_confluence_sides_unresolved")
            self._trace(
                "decision_bar_broke_both_confluence_sides_unresolved",
                bar.ts_close_ns,
                candidate_count=len(candidates),
                rule_provenance=CONFLUENCE_FLIP_RULE,
            )
            return
        if not candidates:
            return

        # One decision bar may clear nested connected clusters.  The cluster
        # with the most independent price facts owns the event; ties select the
        # last barrier crossed in the breakout direction.
        side = candidates[0][0]
        chosen = max(
            candidates,
            key=lambda item: (
                len(item[1]),
                len({zone.family for zone in item[1]}),
                item[3] if side is Side.LONG else -item[2],
                tuple(zone.zone_id for zone in item[1]),
            ),
        )
        _, members, lower, upper = chosen
        primary = self._primary(members)
        setup = self._create_setup(
            path=ScenarioPath.ACCEPTANCE,
            context=primary,
            members=members,
            bar=bar,
            decision_index=index,
            state=SetupState.WAITING_ACCEPTANCE_HOLD,
        )
        if setup is None:
            self._cinc("confluence_break_had_no_executable_objective_or_origin")
            return
        self._cinc("confluence_acceptance_armed")
        self._trace(
            "confluence_acceptance_armed",
            bar.ts_close_ns,
            setup,
            cluster_lower=lower,
            cluster_upper=upper,
            structure_ids=[zone.source_structure_id for zone in members],
            canonical_fact_ids=[
                self._canonical_fact(zone, bar.ts_close_ns) for zone in members
            ],
            structure_families=[zone.family.value for zone in members],
            rule_provenance=CONFLUENCE_FLIP_RULE,
        )

    @property
    def confluence_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._confluence_counts.items())),
            "entry_owner": self.entry_owner_diagnostics,
            "rules": (
                CONFLUENCE_FLIP_RULE,
                ISOLATED_ACCEPTANCE_RESPONSIBILITY_RULE,
            ),
        }


class EasyChartRE1ConfluenceFlipBundle(EasyChartRE1ResponsibleSweepFlowOBBundle):
    """One account stream: reversal core, sweep-valid OBs and confluence flips."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = ReversalOnlyPhaseFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.confluence_flip = ConfluenceAcceptanceEngine(
            symbol,
            tick_size,
            scale_name="CONFLUENCE_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self._audit_offsets["micro"] = 0
        self._audit_offsets["confluence_flip"] = 0
        self._confluence_bundle_counts: dict[str, int] = {}
        self._confluence_bundle_trace: list[dict[str, Any]] = []

    def _binc(self, key: str) -> None:
        self._confluence_bundle_counts[key] = self._confluence_bundle_counts.get(key, 0) + 1

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return super().setups + self.confluence_flip.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return super().plans + self.confluence_flip.plans

    def _route_confluence(self, raw: list[V5TradePlan]) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            if plan.scenario_path != ScenarioPath.ACCEPTANCE.value:
                self._binc("non_acceptance_confluence_plan_suppressed")
                continue
            if self._duplicate_episode(plan):
                self._binc("confluence_plan_overlapped_existing_episode")
                continue
            if not self._route_plan(plan):
                self._binc("confluence_plan_rejected_by_macro_context")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._binc("confluence_plan_allowed")
            self._confluence_bundle_trace.append(
                {
                    "scenario_kind": "confluence_flip_plan_allowed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "interaction_time_ns": plan.interaction_time_ns,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "gross_rr": plan.gross_rr,
                    "rule_provenance": CONFLUENCE_FLIP_RULE,
                }
            )
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        confluence: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
            raw = self.confluence_flip.on_bar(timeframe_minutes, bar)
            self._sync_audit("confluence_flip", self.confluence_flip)
            confluence = self._route_confluence(raw)
        routed = super().on_bar(timeframe_minutes, bar)
        return sorted(
            confluence + routed,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            super().drain_trace()
            + self.confluence_flip.drain_trace()
            + self._confluence_bundle_trace
        )
        self._confluence_bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return super().find_zone(zone_id) or self.confluence_flip.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["confluence_flip_policy"] = {
            "counts": dict(sorted(self._confluence_bundle_counts.items())),
            "engine": self.confluence_flip.confluence_diagnostics,
            "rules": (
                CONFLUENCE_FLIP_RULE,
                ISOLATED_ACCEPTANCE_RESPONSIBILITY_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1ConfluenceFlipBundle
