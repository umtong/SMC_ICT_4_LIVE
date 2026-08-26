"""Contextual five-minute engulfing order blocks with exact one-minute flow.

The supplied human cases consistently use a larger price structure to answer
*where* and a lower-frame engulfing order block to answer *when*:

* a five-minute bearish OB forms at a visible local resistance;
* a five-minute bullish OB completes at the terminal edge of a larger falling
  diagonal/channel;
* a five-minute bullish OB completes during the first retest of a previously
  broken diagonal boundary.

That is a different mechanism from the prior RE1 implementations.  The earlier
candidate either treated a fifteen-minute OB as a standalone decision area or
waited for a one-minute OB/FVG after a five-minute interaction.  Both can lose
the actual human decision: the five-minute engulfing close itself is the
observable transfer event at the pre-existing fifteen-minute structure.

This module gives each scale one responsibility:

* 60m causal structure routes broad direction;
* an ordered 15m trend-line/channel supplies the liquidity location;
* a 5m high-quality engulfing OB must form at that exact projected boundary;
* completed 1m Binance taker flow inside the 5m displacement validates that the
  price move was traded initiative, not only a large candle;
* the stricter TRANSFER variant additionally requires active opposite taker
  pressure in the 5m source candle before aligned initiative in the engulfing
  candle, representing inventory transfer rather than late directional chase;
* entry occurs only after the complete 5m candle and its final constituent 1m
  flow are observable; stop and target remain the natural fixed pre-entry
  geometry already used by RE1.

Ordinary one-minute OB/FVG plans, raw flow substitution, generic horizontal
families and standalone OB decisions cannot reserve the account slot.  No
fitted percentile, clock window, score, session rule, partial position, stop
ratchet or risk change is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup, SetupState, V5TradePlan
from domain import Candle, Side
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from easychart_re1_flow_phase import EasyChartRE1PhaseFlowBundle
from easychart_re1_natural_geometry import NaturalMicroEngine
from easychart_re1_phase import ChannelPhaseStructureBook
from easychart_zones import EasyChartZoneDetector, PriceZone, ZoneKind, ZoneSide


CONTEXTUAL_FIVE_MINUTE_OB_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "A_HIGH_QUALITY_FIVE_MINUTE_ENGULFING_OB_MAY_ENTER_ON_ITS_COMPLETED_CLOSE_ONLY_WHEN_ITS_SOURCE_TOUCHES_A_PREEXISTING_ORDERED_FIFTEEN_MINUTE_BOUNDARY_AND_ITS_IMPULSE_CLOSES_AWAY"
)
CONTEXTUAL_FIVE_MINUTE_OB_FLOW_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "THE_COMPLETED_FIVE_MINUTE_ENGULFING_IMPULSE_REQUIRES_ALIGNED_CUMULATIVE_ONE_MINUTE_TAKER_FLOW_PRICE_PROGRESS_AND_AT_LEAST_ONE_ACTIVE_DIRECTED_PROGRESS_MINUTE"
)
CONTEXTUAL_FIVE_MINUTE_OB_TRANSFER_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "INVENTORY_TRANSFER_REQUIRES_ACTIVE_OPPOSITE_TAKER_PRESSURE_IN_THE_FIVE_MINUTE_SOURCE_CANDLE_BEFORE_ALIGNED_FLOW_IN_THE_ENGULFING_IMPULSE"
)
SAME_CLOSE_FIVE_MINUTE_FLOW_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "THE_FINAL_ONE_MINUTE_FLOW_AT_THE_SAME_CLOSE_AS_A_COMPLETED_FIVE_MINUTE_OB_IS_CAUSALLY_AVAILABLE_BEFORE_ORDER_SUBMISSION"
)
for _rule in (
    CONTEXTUAL_FIVE_MINUTE_OB_RULE,
    SAME_CLOSE_FIVE_MINUTE_FLOW_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)
for _rule in (
    CONTEXTUAL_FIVE_MINUTE_OB_FLOW_RULE,
    CONTEXTUAL_FIVE_MINUTE_OB_TRANSFER_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class ContextualFiveMinuteOBTrigger(str, Enum):
    IMPULSE_FLOW = "CONTEXTUAL_5M_OB_IMPULSE_FLOW"
    TRANSFER_FLOW = "CONTEXTUAL_5M_OB_TRANSFER_FLOW"


@dataclass(frozen=True, slots=True)
class FiveMinuteOBFlowEvidence:
    source_zone_id: str
    source_start_time_ns: int
    source_end_time_ns: int
    impulse_end_time_ns: int
    source_bars: int
    impulse_bars: int
    source_active_opposite_bars: int
    impulse_active_aligned_bars: int
    source_cumulative_signed_taker_quote: float
    impulse_cumulative_signed_taker_quote: float
    impulse_net_price_progress: float
    strongest_impulse_activity_ratio: float
    strongest_impulse_delta_ratio: float
    strongest_impulse_body_ratio: float
    strength: float
    transfer_confirmed: bool


@dataclass(frozen=True, slots=True)
class PendingContextualFiveMinuteOB:
    setup_id: str
    zone_id: str
    observed_time_ns: int
    require_transfer: bool


_REVERSAL_PATHS = {
    ScenarioPath.REJECTION,
    ScenarioPath.BOUNCE,
    ScenarioPath.ROTATION,
}


class ContextualFiveMinuteOBEngine(NaturalMicroEngine):
    """Ordered 15m location whose completed 5m OB and 1m flow trigger entry."""

    def __init__(
        self,
        *args: Any,
        require_transfer: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.structure = ChannelPhaseStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )
        self.flow_analyzer = CausalFlowAnalyzer(self.tick_size)
        self.five_minute_ob_detector = EasyChartZoneDetector(
            self.symbol,
            self.decision_minutes,
            self.tick_size,
        )
        self.require_transfer = require_transfer
        self._pending_contextual_obs: dict[str, PendingContextualFiveMinuteOB] = {}
        self._contextual_counts: dict[str, int] = {}
        self._contextual_trace: list[dict[str, Any]] = []
        self._flow_evidence_by_zone: dict[str, FiveMinuteOBFlowEvidence] = {}

    def _xinc(self, key: str) -> None:
        self._contextual_counts[key] = self._contextual_counts.get(key, 0) + 1

    @staticmethod
    def _side_for_zone(zone: PriceZone) -> Side:
        return Side.LONG if zone.side is ZoneSide.SUPPORT else Side.SHORT

    @staticmethod
    def _aligned(side: Side, value: float) -> bool:
        return value > 0.0 if side is Side.LONG else value < 0.0

    @staticmethod
    def _opposite(side: Side, value: float) -> bool:
        return value < 0.0 if side is Side.LONG else value > 0.0

    @staticmethod
    def _progress(side: Side, start: float, end: float) -> float:
        return end - start if side is Side.LONG else start - end

    def _eligible_setup(self, setup: ScenarioSetup, zone: PriceZone) -> bool:
        if setup.side is not self._side_for_zone(zone):
            return False
        if setup.target_zone is None or setup.target_price is None:
            return False
        if setup.path in _REVERSAL_PATHS:
            return (
                setup.state is SetupState.WAITING_DISPLACEMENT
                and setup.confirmation_time_ns is not None
                and zone.observed_time_ns >= setup.confirmation_time_ns
            )
        if setup.path is ScenarioPath.ACCEPTANCE:
            return (
                setup.state is SetupState.WAITING_ACCEPTANCE_RETEST
                and setup.confirmation_time_ns is not None
                and zone.observed_time_ns > setup.confirmation_time_ns
            )
        return False

    def _formation_touches_setup(
        self,
        setup: ScenarioSetup,
        zone: PriceZone,
    ) -> bool:
        if len(zone.formation_indices) != 2:
            return False
        source_index, impulse_index = zone.formation_indices
        bars = self.five_minute_ob_detector.bars
        if not (
            0 <= source_index < len(bars)
            and 0 <= impulse_index < len(bars)
        ):
            return False
        source = bars[source_index]
        impulse = bars[impulse_index]
        _, lower, upper = self._projected_bounds(setup, source.ts_close_ns)
        source_touches = source.low <= upper and source.high >= lower
        impulse_closes_away = (
            impulse.close > upper
            if setup.side is Side.LONG
            else impulse.close < lower
        )
        directional_impulse = (
            impulse.close > impulse.open
            if setup.side is Side.LONG
            else impulse.close < impulse.open
        )
        if not source_touches:
            self._xinc("five_minute_ob_source_not_at_projected_structure")
        elif not impulse_closes_away:
            self._xinc("five_minute_ob_impulse_did_not_close_away")
        elif not directional_impulse:
            self._xinc("five_minute_ob_impulse_not_directional")
        return source_touches and impulse_closes_away and directional_impulse

    def _register_pending(self, zone: PriceZone) -> None:
        if zone.kind is not ZoneKind.ORDER_BLOCK or not zone.high_quality_by_size:
            return
        candidates = [
            setup
            for setup in self._active.values()
            if self._eligible_setup(setup, zone)
            and self._formation_touches_setup(setup, zone)
        ]
        if not candidates:
            self._xinc("five_minute_ob_without_live_structure_episode")
            return
        setup = max(
            candidates,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.setup_id,
            ),
        )
        key = f"{setup.setup_id}|{zone.zone_id}"
        self._pending_contextual_obs[key] = PendingContextualFiveMinuteOB(
            setup_id=setup.setup_id,
            zone_id=zone.zone_id,
            observed_time_ns=zone.observed_time_ns,
            require_transfer=self.require_transfer,
        )
        self._audit(zone)
        self._xinc("contextual_five_minute_ob_armed")
        self._trace(
            "contextual_five_minute_ob_armed",
            zone.observed_time_ns,
            setup,
            five_minute_ob_zone_id=zone.zone_id,
            five_minute_ob_lower=zone.lower,
            five_minute_ob_upper=zone.upper,
            five_minute_ob_invalidation=zone.invalidation,
            five_minute_ob_strength_ratio=zone.strength_ratio,
            require_transfer=self.require_transfer,
            rule_provenance=(
                CONTEXTUAL_FIVE_MINUTE_OB_RULE,
                CONTEXTUAL_FIVE_MINUTE_OB_FLOW_RULE,
                CONTEXTUAL_FIVE_MINUTE_OB_TRANSFER_RULE,
                SAME_CLOSE_FIVE_MINUTE_FLOW_RULE,
            ),
        )

    def _observations_between(
        self,
        start_time_ns: int,
        end_time_ns: int,
    ) -> list[FlowObservation]:
        return [
            item
            for item in self.flow_analyzer.history
            if start_time_ns < item.ts_close_ns <= end_time_ns
        ]

    def _flow_evidence(
        self,
        zone: PriceZone,
    ) -> FiveMinuteOBFlowEvidence | None:
        if len(zone.formation_indices) != 2:
            return None
        source_index, _ = zone.formation_indices
        bars = self.five_minute_ob_detector.bars
        if source_index <= 0 or source_index >= len(bars):
            self._xinc("five_minute_ob_missing_source_flow_window")
            return None
        source = bars[source_index]
        source_start = bars[source_index - 1].ts_close_ns
        source_flow = self._observations_between(source_start, source.ts_close_ns)
        impulse_flow = self._observations_between(
            source.ts_close_ns,
            zone.observed_time_ns,
        )
        if not source_flow or not impulse_flow:
            self._xinc("five_minute_ob_missing_constituent_one_minute_flow")
            return None

        side = self._side_for_zone(zone)
        impulse_delta = sum(item.signed_taker_quote for item in impulse_flow)
        impulse_progress = self._progress(
            side,
            impulse_flow[0].open,
            impulse_flow[-1].close,
        )
        aligned_impulse = [
            item
            for item in impulse_flow
            if item.active
            and item.directed
            and item.material_progress
            and self._aligned(side, item.signed_taker_quote)
            and (item.body > 0.0 if side is Side.LONG else item.body < 0.0)
        ]
        if not self._aligned(side, impulse_delta):
            self._xinc("five_minute_ob_impulse_cumulative_flow_not_aligned")
            return None
        if impulse_progress <= 0.0:
            self._xinc("five_minute_ob_impulse_price_progress_not_aligned")
            return None
        if not aligned_impulse:
            self._xinc("five_minute_ob_impulse_without_active_directed_progress")
            return None

        source_delta = sum(item.signed_taker_quote for item in source_flow)
        active_opposite_source = [
            item
            for item in source_flow
            if item.active
            and item.directed
            and self._opposite(side, item.signed_taker_quote)
            and (item.body < 0.0 if side is Side.LONG else item.body > 0.0)
        ]
        transfer = self._opposite(side, source_delta) and bool(active_opposite_source)
        if self.require_transfer and not transfer:
            self._xinc("five_minute_ob_source_inventory_transfer_not_observed")
            return None

        strongest = max(
            aligned_impulse,
            key=lambda item: (
                item.activity_ratio * item.delta_ratio * item.body_ratio,
                item.ts_close_ns,
            ),
        )
        evidence = FiveMinuteOBFlowEvidence(
            source_zone_id=zone.zone_id,
            source_start_time_ns=source_start,
            source_end_time_ns=source.ts_close_ns,
            impulse_end_time_ns=zone.observed_time_ns,
            source_bars=len(source_flow),
            impulse_bars=len(impulse_flow),
            source_active_opposite_bars=len(active_opposite_source),
            impulse_active_aligned_bars=len(aligned_impulse),
            source_cumulative_signed_taker_quote=source_delta,
            impulse_cumulative_signed_taker_quote=impulse_delta,
            impulse_net_price_progress=impulse_progress,
            strongest_impulse_activity_ratio=strongest.activity_ratio,
            strongest_impulse_delta_ratio=strongest.delta_ratio,
            strongest_impulse_body_ratio=strongest.body_ratio,
            strength=(
                strongest.activity_ratio
                * strongest.delta_ratio
                * strongest.body_ratio
            ),
            transfer_confirmed=transfer,
        )
        self._flow_evidence_by_zone[zone.zone_id] = evidence
        return evidence

    def _five_minute_bar_for_zone(self, zone: PriceZone) -> Candle:
        return self.five_minute_ob_detector.bars[zone.formation_indices[-1]]

    def _target_was_spent_inside_ob_bar(
        self,
        setup: ScenarioSetup,
        zone: PriceZone,
    ) -> bool:
        if setup.target_price is None:
            return True
        impulse = self._five_minute_bar_for_zone(zone)
        return (
            impulse.high >= setup.target_price
            if setup.side is Side.LONG
            else impulse.low <= setup.target_price
        )

    def _rewrite_family(
        self,
        plan: V5TradePlan,
        setup: ScenarioSetup,
        evidence: FiveMinuteOBFlowEvidence,
    ) -> V5TradePlan:
        mechanism = "TRANSFER" if evidence.transfer_confirmed else "IMPULSE"
        family = f"MICRO_CONTEXTUAL_5M_OB_{setup.path.value}_{mechanism}"
        rewritten = replace(
            plan,
            family=family,
            causal_event_id=f"{family}:{setup.setup_id}",
        )
        if self.plans and self.plans[-1].plan_id == plan.plan_id:
            self.plans[-1] = rewritten
        return rewritten

    def _create_contextual_plan(
        self,
        setup: ScenarioSetup,
        zone: PriceZone,
        bar: Candle,
        evidence: FiveMinuteOBFlowEvidence,
    ) -> V5TradePlan | None:
        if self._target_was_spent_inside_ob_bar(setup, zone):
            self._xinc("five_minute_ob_target_spent_before_close_entry")
            return None
        if setup.side is Side.LONG:
            stop = min(setup.interaction_extreme - self.tick_size, zone.invalidation)
        else:
            stop = max(setup.interaction_extreme + self.tick_size, zone.invalidation)
        trigger = (
            ContextualFiveMinuteOBTrigger.TRANSFER_FLOW
            if evidence.transfer_confirmed
            else ContextualFiveMinuteOBTrigger.IMPULSE_FLOW
        )
        plan = self._make_plan(
            setup,
            bar,
            entry=bar.close,
            stop=stop,
            trigger_zone=zone,
            trigger_kind=trigger,
            trigger_strength=evidence.strength,
        )
        if plan is None:
            self._xinc("contextual_five_minute_ob_geometry_rejected")
            return None
        plan = self._rewrite_family(plan, setup, evidence)
        self._xinc("contextual_five_minute_ob_plan_created")
        self._trace(
            "contextual_five_minute_ob_plan_created",
            bar.ts_close_ns,
            setup,
            plan_id=plan.plan_id,
            family=plan.family,
            five_minute_ob_zone_id=zone.zone_id,
            five_minute_ob_strength_ratio=zone.strength_ratio,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            gross_rr=plan.gross_rr,
            source_active_opposite_bars=evidence.source_active_opposite_bars,
            impulse_active_aligned_bars=evidence.impulse_active_aligned_bars,
            source_cumulative_signed_taker_quote=(
                evidence.source_cumulative_signed_taker_quote
            ),
            impulse_cumulative_signed_taker_quote=(
                evidence.impulse_cumulative_signed_taker_quote
            ),
            impulse_net_price_progress=evidence.impulse_net_price_progress,
            transfer_confirmed=evidence.transfer_confirmed,
            rule_provenance=(
                CONTEXTUAL_FIVE_MINUTE_OB_RULE,
                CONTEXTUAL_FIVE_MINUTE_OB_FLOW_RULE,
                CONTEXTUAL_FIVE_MINUTE_OB_TRANSFER_RULE,
                SAME_CLOSE_FIVE_MINUTE_FLOW_RULE,
            ),
        )
        return plan

    def _process_pending_at_close(
        self,
        bar: Candle,
    ) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        for key, pending in list(self._pending_contextual_obs.items()):
            if pending.observed_time_ns > bar.ts_close_ns:
                continue
            self._pending_contextual_obs.pop(key, None)
            if pending.observed_time_ns < bar.ts_close_ns:
                self._xinc("contextual_five_minute_ob_same_close_missed")
                continue
            setup = self._active.get(pending.setup_id)
            if setup is None:
                self._xinc("contextual_five_minute_ob_setup_not_active_at_close")
                continue
            zone = next(
                (
                    item
                    for item in self.five_minute_ob_detector.zones
                    if item.zone_id == pending.zone_id
                ),
                None,
            )
            if zone is None:
                raise RuntimeError("pending five-minute OB lost its source zone")
            evidence = self._flow_evidence(zone)
            if evidence is None:
                continue
            plan = self._create_contextual_plan(setup, zone, bar, evidence)
            if plan is not None:
                output.append(plan)
                for other_key, other in list(self._pending_contextual_obs.items()):
                    if other.setup_id == setup.setup_id:
                        self._pending_contextual_obs.pop(other_key, None)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == self.decision_minutes:
            # First let the completed decision bar advance/create its causal
            # structure episode, then attach any 5m OB formed by that same close.
            plans = super().on_bar(timeframe_minutes, bar)
            if plans:
                raise RuntimeError("decision frame unexpectedly emitted a plan")
            for zone in self.five_minute_ob_detector.on_bar(bar):
                self._audit(zone)
                self._register_pending(zone)
            return []

        if timeframe_minutes != self.trigger_minutes:
            return super().on_bar(timeframe_minutes, bar)

        # Do not invoke the inherited 1m visual/flow entry machinery.  Keep the
        # 1m detector history for audit, observe exact extended-kline flow, and
        # evaluate only a 5m OB whose close timestamp is this timestamp.
        self._current_trigger_bar = bar
        try:
            for zone in self.trigger_detector.on_bar(bar):
                self._audit(zone)
            self.flow_analyzer.observe(bar)
            return self._process_pending_at_close(bar)
        finally:
            self._current_trigger_bar = None

    @property
    def focused_flow_diagnostics(self) -> dict[str, Any]:
        # Compatibility with the parent phase bundle diagnostics.
        return {
            "reversal_entry": "CONTEXTUAL_5M_OB_COMPLETED_CLOSE",
            "acceptance_entry": "CONTEXTUAL_5M_OB_ON_FIRST_FLIPPED_BOUNDARY_RETEST",
            "one_minute_visual_entries": "SUPPRESSED",
            "raw_flow_substitution": "SUPPRESSED",
            "require_transfer": self.require_transfer,
            "rules": (
                CONTEXTUAL_FIVE_MINUTE_OB_RULE,
                CONTEXTUAL_FIVE_MINUTE_OB_FLOW_RULE,
                CONTEXTUAL_FIVE_MINUTE_OB_TRANSFER_RULE,
                SAME_CLOSE_FIVE_MINUTE_FLOW_RULE,
            ),
        }

    @property
    def contextual_five_minute_ob_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._contextual_counts.items())),
            "pending_at_end": len(self._pending_contextual_obs),
            "flow_validated_zones": len(self._flow_evidence_by_zone),
            "require_transfer": self.require_transfer,
            "five_minute_detector": dict(self.five_minute_ob_detector.diagnostics),
            "flow_analyzer": self.flow_analyzer.diagnostics,
            "rules": (
                CONTEXTUAL_FIVE_MINUTE_OB_RULE,
                CONTEXTUAL_FIVE_MINUTE_OB_FLOW_RULE,
                CONTEXTUAL_FIVE_MINUTE_OB_TRANSFER_RULE,
                SAME_CLOSE_FIVE_MINUTE_FLOW_RULE,
            ),
        }


class ContextualFiveMinuteOBImpulseEngine(ContextualFiveMinuteOBEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, require_transfer=False, **kwargs)


class ContextualFiveMinuteOBTransferEngine(ContextualFiveMinuteOBEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, require_transfer=True, **kwargs)


class _ContextualFiveMinuteOBBundleBase(EasyChartRE1PhaseFlowBundle):
    engine_cls = ContextualFiveMinuteOBImpulseEngine
    candidate_name = "candidate-easychart_re1_contextual_5m_ob"

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = self.engine_cls(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0
        self._contextual_bundle_counts: dict[str, int] = {}

    def _binc(self, key: str) -> None:
        self._contextual_bundle_counts[key] = (
            self._contextual_bundle_counts.get(key, 0) + 1
        )

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return self.micro.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return self.micro.plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        # Preserve the already-audited 60m and 15m state books, but do not call
        # the broad parent family router.  Only the contextual 5m OB engine can
        # create a plan or claim an episode.
        if timeframe_minutes == self.CONTEXT_MINUTES:
            self._update_macro_context(bar)
            return []
        if timeframe_minutes == self.LOCAL_CONTEXT_MINUTES:
            self._update_local_direction(bar)
            self._update_decision_footprints(bar)
        if timeframe_minutes not in {15, 5, 1}:
            return []

        raw = self.micro.on_bar(timeframe_minutes, bar)
        self._sync_audit("micro", self.micro)
        output: list[V5TradePlan] = []
        for plan in sorted(
            raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            if self._duplicate_episode(plan):
                self._binc("contextual_five_minute_ob_duplicate_episode")
                continue
            if not self._route_plan(plan):
                self._binc("contextual_five_minute_ob_rejected_by_macro_context")
                continue
            self._claim_episode(plan)
            output.append(plan)
            self._binc("contextual_five_minute_ob_allowed")
        return output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = self.micro.drain_trace() + self._bundle_trace
        self._bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> Any | None:
        return self.micro.find_zone(zone_id)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["contextual_five_minute_ob_bundle"] = {
            "candidate": self.candidate_name,
            "counts": dict(sorted(self._contextual_bundle_counts.items())),
            "engine": self.micro.contextual_five_minute_ob_diagnostics,
            "rules": (
                CONTEXTUAL_FIVE_MINUTE_OB_RULE,
                CONTEXTUAL_FIVE_MINUTE_OB_FLOW_RULE,
                CONTEXTUAL_FIVE_MINUTE_OB_TRANSFER_RULE,
                SAME_CLOSE_FIVE_MINUTE_FLOW_RULE,
            ),
        }
        return output


class EasyChartRE1ContextualFiveMinuteOBBundle(
    _ContextualFiveMinuteOBBundleBase,
):
    engine_cls = ContextualFiveMinuteOBImpulseEngine
    candidate_name = "candidate-easychart_re1_contextual_5m_ob"


class EasyChartRE1ContextualFiveMinuteOBTransferBundle(
    _ContextualFiveMinuteOBBundleBase,
):
    engine_cls = ContextualFiveMinuteOBTransferEngine
    candidate_name = "candidate-easychart_re1_contextual_5m_ob_transfer"


MultiScaleScenarioBundle = EasyChartRE1ContextualFiveMinuteOBBundle
