"""Unified structure, liquidity-event, price-volume auction controller.

This candidate is not another plan score or a wrapper around separate OB/FVG,
channel, and fakeout strategies.  A pre-existing 15-minute public structure owns
one causal auction episode.  The episode can evolve through rejection,
apparent acceptance, delayed trap reclaim, displacement, and the first
one-minute footprint return.  Only the completed state of that same episode may
produce one immutable entry/stop/target plan.

The inherited EasyChart natural-geometry policy supplies causal trend lines,
channels, repeated horizontal defense, major swing liquidity, event-local
OB/FVG entry refinement, decision-swing invalidation, and the nearest meaningful
5m/15m/channel objective.  This module changes two structural responsibilities:

* an apparent accepted break which fails its hold/retest is not discarded; the
  same source-owned episode becomes a delayed Trap and must reclaim, transfer
  control, and complete its first footprint response before entry;
* price-only completion is insufficient.  Completed Binance aggressor flow must
  identify either initiative in the intended direction or absorption of
  opposing aggression at the public boundary.  Volume is interpreted as effort
  versus result inside the episode, not as a fitted global threshold.

NautilusTrader remains the authority for orders, fills, fees, one-account
routing, 3% risk sizing, and continuous NAV.  No trade quota, clock exit, daily
loss cap, partial exit, stop movement, target movement, or symbol-specific rule
is introduced here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

import contracts_v5 as _contracts
from contracts_v5 import (
    ScenarioPath,
    ScenarioSetup,
    SetupState,
    StructureFamily,
    V5TradePlan,
)
from domain import Candle, Side
from easychart_re1_flow import CausalFlowAnalyzer, FlowObservation
from easychart_re1_natural_geometry import (
    EasyChartRE1NaturalGeometryBundle,
    NaturalHorizontalEngine,
    NaturalMajorSwingEngine,
    NaturalMicroEngine,
)


FAILED_ACCEPTANCE_TRAP_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:APPARENT_ACCEPTANCE_THAT_FAILS_HOLD_OR_FIRST_"
    "RETEST_REMAINS_THE_SAME_CAUSAL_EPISODE_AND_TRANSITIONS_TO_DELAYED_TRAP_RECLAIM"
)
PRICE_VOLUME_CONTROL_RULE = (
    "RESEARCH_HYPOTHESIS:AN_EXECUTABLE_STRUCTURE_EPISODE_REQUIRES_COMPLETED_"
    "AGGRESSOR_INITIATIVE_OR_OPPOSING_AGGRESSION_ABSORPTION_AT_ITS_PUBLIC_BOUNDARY"
)
EFFORT_RESULT_RULE = (
    "EXTERNAL_METHOD:AUCTION_EFFORT_IS_BINANCE_QUOTE_VOLUME_AND_TAKER_IMBALANCE_"
    "WHILE_RESULT_IS_CAUSAL_PRICE_PROGRESS_RELATIVE_TO_THE_OWNED_STRUCTURE"
)
SINGLE_EPISODE_STATE_RULE = (
    "IMPLEMENTATION_VALIDITY:ONE_FIRST_PUBLIC_STRUCTURE_INTERACTION_RETAINS_ONE_"
    "OWNER_ACROSS_REJECTION_ACCEPTANCE_TRAP_AND_ENTRY_STATES"
)
for _rule in (FAILED_ACCEPTANCE_TRAP_RULE, SINGLE_EPISODE_STATE_RULE):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)
if PRICE_VOLUME_CONTROL_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (PRICE_VOLUME_CONTROL_RULE,)
if EFFORT_RESULT_RULE not in _contracts.EXTERNAL_RULES:
    _contracts.EXTERNAL_RULES += (EFFORT_RESULT_RULE,)


@dataclass(frozen=True, slots=True)
class EpisodeFlowControl:
    mechanism: str
    episode_bars: int
    event_bars: int
    response_bars: int
    total_quote: float
    aligned_taker_quote: float
    adverse_taker_quote: float
    cumulative_signed_for_side: float
    adverse_penetration: float
    recovery_from_extreme: float
    final_control_progress: float
    event_active_directed_bars: int
    response_active_directed_bars: int
    current_activity_ratio: float
    current_delta_ratio: float
    current_impact_per_activity: float


class StructuralAuctionMixin:
    """Turn the v5 structure engine into one episode-owned auction state machine."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.auction_flow = CausalFlowAnalyzer(self.tick_size)
        self._trap_setup_ids: set[str] = set()
        self._failed_acceptance_retest_ids: set[str] = set()
        self._structural_counts: dict[str, int] = {}
        self._plan_flow: dict[str, EpisodeFlowControl] = {}

    def _sinc(self, key: str) -> None:
        self._structural_counts[key] = self._structural_counts.get(key, 0) + 1

    @staticmethod
    def _intended_sign(side: Side) -> float:
        return 1.0 if side is Side.LONG else -1.0

    @staticmethod
    def _active_directed(items: Iterable[FlowObservation]) -> int:
        return sum(item.active and item.directed for item in items)

    def _finish(
        self,
        setup: ScenarioSetup,
        state: SetupState,
        time_ns: int,
        reason: str,
        **values: Any,
    ) -> None:
        self._trap_setup_ids.discard(setup.setup_id)
        self._failed_acceptance_retest_ids.discard(setup.setup_id)
        super()._finish(setup, state, time_ns, reason, **values)

    def _trap_target(
        self,
        setup: ScenarioSetup,
        bar: Candle,
        side: Side,
    ) -> tuple[Any, float, str | None, float | None] | None:
        channel_members = [
            member
            for member in setup.context_members
            if member.family is StructureFamily.CHANNEL
        ]
        target_context = (
            max(
                channel_members,
                key=lambda item: (
                    item.source_pivot_span,
                    item.strength_ratio,
                    item.zone_id,
                ),
            )
            if channel_members
            else setup.context
        )
        return self._select_target(
            target_context,
            side,
            ScenarioPath.REJECTION,
            bar,
        )

    def _episode_extreme(self, setup: ScenarioSetup, index: int, side: Side) -> float:
        start = max(0, setup.interaction_index)
        bars = self.decision_bars[start : index + 1]
        if not bars:
            return setup.interaction_extreme
        return (
            min(bar.low for bar in bars)
            if side is Side.LONG
            else max(bar.high for bar in bars)
        )

    def _convert_failed_acceptance(
        self,
        setup: ScenarioSetup,
        bar: Candle,
        index: int,
        reason: str,
    ) -> bool:
        """Reuse the accepted-break owner as the reverse Trap owner.

        The source structure was already claimed at the first interaction.  A
        new setup would double-count the same stop cascade.  Mutating the same
        setup preserves event identity, target freshness, and one-owner routing.
        """
        reverse_side = self._side_for_zone(setup.context)
        target = self._trap_target(setup, bar, reverse_side)
        if target is None:
            self._finish(
                setup,
                SetupState.NO_TARGET,
                bar.ts_close_ns,
                "trap_no_preexisting_opposing_liquidity",
                failed_acceptance_reason=reason,
                rule_provenance=FAILED_ACCEPTANCE_TRAP_RULE,
            )
            return False

        target_zone, target_price, channel_id, midline = target
        setup.path = ScenarioPath.REJECTION
        setup.side = reverse_side
        setup.state = SetupState.WAITING_RECLAIM
        setup.interaction_extreme = self._episode_extreme(setup, index, reverse_side)
        setup.target_zone = target_zone
        setup.target_price = target_price
        setup.confirmation_time_ns = None
        setup.acceptance_break_index = None
        setup.acceptance_origin = None
        setup.trigger_zone = None
        setup.trigger_index = None
        setup.channel_id = channel_id
        setup.midline_price_at_interaction = midline
        setup.first_retest_consumed = False
        setup.terminal_reason = None
        self._trap_setup_ids.add(setup.setup_id)
        self._failed_acceptance_retest_ids.discard(setup.setup_id)
        self._audit(target_zone)
        self._sinc("failed_acceptance_converted_to_trap")
        self._trace(
            "failed_acceptance_converted_to_same_episode_trap",
            bar.ts_close_ns,
            setup,
            failed_acceptance_reason=reason,
            reverse_side=reverse_side.name,
            trap_extreme=setup.interaction_extreme,
            target_zone_id=target_zone.zone_id,
            target_price=target_price,
            rule_provenance=(
                FAILED_ACCEPTANCE_TRAP_RULE,
                SINGLE_EPISODE_STATE_RULE,
            ),
        )
        return True

    def _advance_decision_setups(self, bar: Candle, index: int) -> None:
        # Convert failed accepted breaks before the inherited transition method
        # terminally discards them.  The inherited WAITING_RECLAIM transition can
        # then confirm an immediate reclaim on this same completed decision bar.
        for setup in list(self._active.values()):
            if setup.state is SetupState.WAITING_ACCEPTANCE_HOLD:
                expected = (
                    -1
                    if setup.acceptance_break_index is None
                    else setup.acceptance_break_index
                ) + 1
                if index < expected:
                    continue
                _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
                held = (
                    bar.open > upper and bar.close > upper
                    if setup.side is Side.LONG
                    else bar.open < lower and bar.close < lower
                )
                if not held:
                    self._convert_failed_acceptance(
                        setup,
                        bar,
                        index,
                        "NEXT_DECISION_BAR_DID_NOT_HOLD_OUTSIDE",
                    )
                    continue

            if setup.state is SetupState.WAITING_ACCEPTANCE_RETEST:
                _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
                closed_back_inside = (
                    bar.close < lower
                    if setup.side is Side.LONG
                    else bar.close > upper
                )
                first_retest_failed = setup.setup_id in self._failed_acceptance_retest_ids
                if closed_back_inside or first_retest_failed:
                    reason = (
                        "ACCEPTED_BREAK_CLOSED_BACK_INSIDE"
                        if closed_back_inside
                        else "FIRST_TRIGGER_RETEST_FAILED_TO_HOLD"
                    )
                    self._convert_failed_acceptance(setup, bar, index, reason)

        super()._advance_decision_setups(bar, index)

    def _advance_acceptance_retests(
        self,
        bar: Candle,
        index: int,
    ) -> list[V5TradePlan]:
        """Keep a failed first acceptance retest alive for Trap classification.

        The base engine ends the setup on a one-minute close back inside.  That
        is exactly the observable start of a delayed Trap, but the final reclaim
        belongs to the completed five-minute decision bar.  We therefore mark
        the failure, prevent a later acceptance entry, and let the next decision
        update convert the same episode.
        """
        output: list[V5TradePlan] = []
        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_ACCEPTANCE_RETEST:
                continue
            if setup.confirmation_time_ns is None or bar.ts_close_ns <= setup.confirmation_time_ns:
                continue
            if self._target_is_spent(setup, bar):
                self._finish(
                    setup,
                    SetupState.TARGET_SPENT,
                    bar.ts_close_ns,
                    "target_spent_before_acceptance_or_trap_entry",
                )
                continue
            if setup.setup_id in self._failed_acceptance_retest_ids:
                continue
            _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
            touched = bar.low <= upper and bar.high >= lower
            if not touched:
                continue
            closes_outside = (
                bar.close > upper
                if setup.side is Side.LONG
                else bar.close < lower
            )
            if not closes_outside:
                self._failed_acceptance_retest_ids.add(setup.setup_id)
                self._sinc("acceptance_first_retest_became_trap_watch")
                self._trace(
                    "acceptance_first_retest_failed_trap_watch",
                    bar.ts_close_ns,
                    setup,
                    retest_open=bar.open,
                    retest_high=bar.high,
                    retest_low=bar.low,
                    retest_close=bar.close,
                    projected_lower=lower,
                    projected_upper=upper,
                    rule_provenance=FAILED_ACCEPTANCE_TRAP_RULE,
                )
                continue
            stop = self._acceptance_stop(setup, bar.ts_close_ns)
            if stop is None:
                self._finish(
                    setup,
                    SetupState.NO_TRADE_GEOMETRY,
                    bar.ts_close_ns,
                    "acceptance_missing_structural_stop",
                )
                continue
            proxy = self.structure.snapshot_for(setup.context, bar.ts_close_ns)
            self._audit(proxy)
            plan = self._make_plan(
                setup,
                bar,
                entry=bar.close,
                stop=stop,
                trigger_zone=proxy,
                trigger_kind=proxy.kind,
                trigger_strength=proxy.strength_ratio,
            )
            if plan is not None:
                output.append(plan)
        return output

    def _episode_observations(self, setup: ScenarioSetup, time_ns: int) -> list[FlowObservation]:
        return [
            item
            for item in self.auction_flow.history
            if setup.interaction_time_ns < item.ts_close_ns <= time_ns
        ]

    def _flow_control(
        self,
        setup: ScenarioSetup,
        bar: Candle,
    ) -> EpisodeFlowControl | None:
        observations = self._episode_observations(setup, bar.ts_close_ns)
        if not observations:
            return None
        current = observations[-1]
        if current.ts_close_ns != bar.ts_close_ns:
            return None

        sign = self._intended_sign(setup.side)
        _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
        boundary = upper if setup.side is Side.LONG else lower
        final_progress = sign * (bar.close - boundary)
        if final_progress <= 0.0:
            return None

        total_quote = sum(item.quote_volume for item in observations)
        if total_quote <= 0.0:
            return None
        signed_for_side = [sign * item.signed_taker_quote for item in observations]
        aligned_quote = sum(max(0.0, value) for value in signed_for_side)
        adverse_quote = sum(max(0.0, -value) for value in signed_for_side)
        cumulative = sum(signed_for_side)

        episode_low = min(item.low for item in observations)
        episode_high = max(item.high for item in observations)
        if setup.side is Side.LONG:
            adverse_penetration = max(0.0, lower - episode_low)
            recovery = bar.close - episode_low
        else:
            adverse_penetration = max(0.0, episode_high - upper)
            recovery = episode_high - bar.close

        confirmation = setup.confirmation_time_ns or setup.interaction_time_ns
        event = [item for item in observations if item.ts_close_ns <= confirmation]
        response = [item for item in observations if item.ts_close_ns > confirmation]
        meaningful_event = [item for item in event if item.active and item.directed]
        meaningful_response = [item for item in response if item.active and item.directed]
        meaningful_episode = meaningful_event + meaningful_response
        if not meaningful_episode:
            return None

        aligned_response = any(
            sign * item.signed_taker_quote > 0.0 and item.material_progress
            for item in meaningful_response
        )
        adverse_event = any(
            sign * item.signed_taker_quote < 0.0
            for item in meaningful_event
        )
        adverse_response = any(
            sign * item.signed_taker_quote < 0.0
            for item in meaningful_response
        )

        # Initiative owns price progress when meaningful aligned aggression is
        # present after confirmation and cumulative intended flow is not losing
        # to the opposite side.  Absorption owns the episode when meaningful
        # opposing aggression exists but price recovers farther than the adverse
        # penetration and closes in control.  Both are effort-versus-result
        # relations; neither is a fitted activity or delta threshold.
        mechanism: str | None = None
        if aligned_response and cumulative >= 0.0:
            mechanism = "AGGRESSOR_INITIATIVE_CONTROL"
        elif (
            (adverse_event or adverse_response)
            and cumulative < 0.0
            and recovery > adverse_penetration
        ):
            mechanism = "OPPOSING_AGGRESSION_ABSORBED"
        elif (
            aligned_response
            and recovery > adverse_penetration
            and aligned_quote > 0.0
        ):
            mechanism = "INITIATIVE_AFTER_ABSORPTION"
        if mechanism is None:
            return None

        return EpisodeFlowControl(
            mechanism=mechanism,
            episode_bars=len(observations),
            event_bars=len(event),
            response_bars=len(response),
            total_quote=total_quote,
            aligned_taker_quote=aligned_quote,
            adverse_taker_quote=adverse_quote,
            cumulative_signed_for_side=cumulative,
            adverse_penetration=adverse_penetration,
            recovery_from_extreme=recovery,
            final_control_progress=final_progress,
            event_active_directed_bars=len(meaningful_event),
            response_active_directed_bars=len(meaningful_response),
            current_activity_ratio=current.activity_ratio,
            current_delta_ratio=current.delta_ratio,
            current_impact_per_activity=current.impact_per_activity,
        )

    @staticmethod
    def _family_owner(setup: ScenarioSetup, trap: bool) -> str:
        if trap:
            return "STRUCTURAL_TRAP_RECLAIM"
        return {
            ScenarioPath.REJECTION: "STRUCTURAL_SHARP_REJECTION",
            ScenarioPath.ACCEPTANCE: "STRUCTURAL_ACCEPTANCE_RETEST",
            ScenarioPath.ROTATION: "STRUCTURAL_CHANNEL_ROTATION",
            ScenarioPath.BOUNCE: "STRUCTURAL_STRUCTURE_BOUNCE",
        }[setup.path]

    def _make_plan(
        self,
        setup: ScenarioSetup,
        bar: Candle,
        *,
        entry: float,
        stop: float,
        trigger_zone: Any,
        trigger_kind: Any,
        trigger_strength: float,
    ) -> V5TradePlan | None:
        flow = self._flow_control(setup, bar)
        if flow is None:
            self._sinc("price_complete_without_volume_control")
            self._finish(
                setup,
                SetupState.NO_TRADE_GEOMETRY,
                bar.ts_close_ns,
                "price_volume_control_not_confirmed",
                rule_provenance=(PRICE_VOLUME_CONTROL_RULE, EFFORT_RESULT_RULE),
            )
            return None

        trap = setup.setup_id in self._trap_setup_ids
        owner = self._family_owner(setup, trap)
        plan = super()._make_plan(
            setup,
            bar,
            entry=entry,
            stop=stop,
            trigger_zone=trigger_zone,
            trigger_kind=trigger_kind,
            trigger_strength=trigger_strength,
        )
        if plan is None:
            return None
        structural = replace(
            plan,
            plan_id=f"sac-v2-{plan.plan_id}",
            causal_event_id=f"{owner}:{plan.causal_event_id}",
            family=f"SAC_V2_{owner}:{plan.family}",
            rule_provenance=plan.rule_provenance
            + (
                FAILED_ACCEPTANCE_TRAP_RULE,
                PRICE_VOLUME_CONTROL_RULE,
                EFFORT_RESULT_RULE,
                SINGLE_EPISODE_STATE_RULE,
            ),
        )
        # The inherited engine already stored the original immutable plan.  Keep
        # its audit list consistent with the object returned to routing/execution.
        if self.plans and self.plans[-1].plan_id == plan.plan_id:
            self.plans[-1] = structural
        self._plan_flow[structural.plan_id] = flow
        self._sinc(f"plan_{owner.lower()}")
        self._sinc(f"flow_{flow.mechanism.lower()}")
        self._trace(
            "structural_auction_plan_created",
            bar.ts_close_ns,
            setup,
            plan_id=structural.plan_id,
            family=structural.family,
            entry=structural.entry,
            stop=structural.stop,
            target=structural.target,
            gross_rr=structural.gross_rr,
            trap=trap,
            flow_mechanism=flow.mechanism,
            flow_episode_bars=flow.episode_bars,
            flow_event_bars=flow.event_bars,
            flow_response_bars=flow.response_bars,
            flow_total_quote=flow.total_quote,
            flow_aligned_taker_quote=flow.aligned_taker_quote,
            flow_adverse_taker_quote=flow.adverse_taker_quote,
            flow_cumulative_signed_for_side=flow.cumulative_signed_for_side,
            flow_adverse_penetration=flow.adverse_penetration,
            flow_recovery_from_extreme=flow.recovery_from_extreme,
            flow_final_control_progress=flow.final_control_progress,
            flow_event_active_directed_bars=flow.event_active_directed_bars,
            flow_response_active_directed_bars=flow.response_active_directed_bars,
            flow_current_activity_ratio=flow.current_activity_ratio,
            flow_current_delta_ratio=flow.current_delta_ratio,
            flow_current_impact_per_activity=flow.current_impact_per_activity,
            rule_provenance=(
                FAILED_ACCEPTANCE_TRAP_RULE,
                PRICE_VOLUME_CONTROL_RULE,
                EFFORT_RESULT_RULE,
                SINGLE_EPISODE_STATE_RULE,
            ),
        )
        self._trap_setup_ids.discard(setup.setup_id)
        self._failed_acceptance_retest_ids.discard(setup.setup_id)
        return structural

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == self.trigger_minutes:
            self.auction_flow.observe(bar)
        return super().on_bar(timeframe_minutes, bar)

    @property
    def structural_auction_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._structural_counts.items())),
            "active_trap_setups": len(self._trap_setup_ids),
            "active_failed_acceptance_retests": len(self._failed_acceptance_retest_ids),
            "flow": self.auction_flow.diagnostics,
            "plan_flow": {
                plan_id: {
                    "mechanism": item.mechanism,
                    "episode_bars": item.episode_bars,
                    "event_bars": item.event_bars,
                    "response_bars": item.response_bars,
                    "aligned_taker_quote": item.aligned_taker_quote,
                    "adverse_taker_quote": item.adverse_taker_quote,
                    "final_control_progress": item.final_control_progress,
                }
                for plan_id, item in self._plan_flow.items()
            },
            "rules": (
                FAILED_ACCEPTANCE_TRAP_RULE,
                PRICE_VOLUME_CONTROL_RULE,
                EFFORT_RESULT_RULE,
                SINGLE_EPISODE_STATE_RULE,
            ),
        }


class StructuralMicroEngine(StructuralAuctionMixin, NaturalMicroEngine):
    pass


class StructuralHorizontalEngine(StructuralAuctionMixin, NaturalHorizontalEngine):
    pass


class StructuralMajorSwingEngine(StructuralAuctionMixin, NaturalMajorSwingEngine):
    pass


class StructuralAuctionControlV1Bundle(EasyChartRE1NaturalGeometryBundle):
    """One integrated multi-structure account stream with shared state semantics."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = StructuralMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = StructuralHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = StructuralMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "horizontal", "major_swing"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["structural_auction_control_v1"] = {
            "micro": self.micro.structural_auction_diagnostics,
            "horizontal": self.horizontal.structural_auction_diagnostics,
            "major_swing": self.major_swing.structural_auction_diagnostics,
            "policy": (
                "one public structure interaction -> rejection, apparent acceptance, "
                "same-episode trap conversion, completed price-volume control, first "
                "OB/FVG or exact retest, structural stop, nearest causal objective"
            ),
            "rules": (
                FAILED_ACCEPTANCE_TRAP_RULE,
                PRICE_VOLUME_CONTROL_RULE,
                EFFORT_RESULT_RULE,
                SINGLE_EPISODE_STATE_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = StructuralAuctionControlV1Bundle
