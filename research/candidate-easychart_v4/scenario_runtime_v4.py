"""Causal runtime binding for the EasyChart v4 structural scenario.

A trigger OB/FVG can finish on the same closed lower-timeframe bar which first
makes a known structural interaction observable. The footprint is armed on
that bar, but entry remains impossible until a later first retest.

A context-timeframe Fakeout is only an interaction candidate. EasyChart's
material describes a strong, immediate opposite move after the sweep. The
exact machine translation used here is deliberately visible: the next context
candle must close beyond the Fakeout candle's opposite extreme before a lower-
timeframe displacement is allowed to arm a trade.

EasyChart's top-down workflow requires a higher-timeframe reaction or state
transition before a lower-timeframe entry.  The router therefore follows the
latest confirmed, still-live 1h structural event rather than the slope of an
older channel.  The event remains live only until its own structural stop or
objective is reached.  This is a state router, not a score or risk multiplier;
unresolved context means no micro trade, while native 1h->5m plans remain
available.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from domain import Candle, Side
from easychart_mtf_scenario import MTFTradePlan
from easychart_zones import PriceZone
from market_structure import StructureEvent, StructurePath
from scenario_bundle_v4 import (
    ResearchScenarioBundleV4 as _BaseResearchScenarioBundleV4,
    StructuralScenarioEngine,
    StructuralSetupState,
    _EvidenceDetectorView,
)


class CausalStructuralScenarioEngine(StructuralScenarioEngine):
    SOURCE_RULES = StructuralScenarioEngine.SOURCE_RULES + (
        "SOURCE_EXPLICIT:FAKEOUT_REVERSES_RAPIDLY_AFTER_RECLAIM",
    )
    TRANSLATION_RULES = StructuralScenarioEngine.TRANSLATION_RULES + (
        "HUMAN_NATURAL_INFERENCE:NEXT_CONTEXT_CLOSE_BEYOND_FAKEOUT_OPPOSITE_EXTREME_CONFIRMS_REVERSAL",
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pending_fakeout_confirmation: dict[str, float] = {}
        self._active_context_event: StructureEvent | None = None
        self._active_context_confirmed_time_ns: int | None = None
        self._active_context_basis = "UNRESOLVED_1H_EVENT_CONTEXT"

    def _context_target(self, event: StructureEvent, ts_ns: int) -> float | None:
        if event.target_boundary_id is None:
            return None
        boundary = self.structure.find_boundary(event.target_boundary_id)
        return None if boundary is None else boundary.level_at(ts_ns)

    def _clear_context(self, *, time_ns: int, reason: str, **values) -> None:
        event = self._active_context_event
        if event is None:
            return
        self._inc(reason)
        self._trace(
            reason,
            time_ns,
            event_id=event.event_id,
            context_side=event.side.name,
            context_path=event.path.value,
            context_structure_kind=event.structure_kind.value,
            context_stop=event.stop_reference,
            context_target=self._context_target(event, time_ns),
            **values,
        )
        self._active_context_event = None
        self._active_context_confirmed_time_ns = None
        self._active_context_basis = "UNRESOLVED_1H_EVENT_CONTEXT"

    def _activate_context(
        self,
        event: StructureEvent,
        *,
        confirmed_time_ns: int,
        reference_price: float,
        reason: str,
    ) -> None:
        target = self._context_target(event, confirmed_time_ns)
        valid = (
            target is not None
            and (
                event.stop_reference < reference_price < target
                if event.side is Side.LONG
                else target < reference_price < event.stop_reference
            )
        )
        if not valid:
            self._inc("context_event_rejected_invalid_geometry")
            self._trace(
                "context_event_rejected_invalid_geometry",
                confirmed_time_ns,
                event_id=event.event_id,
                context_side=event.side.name,
                context_path=event.path.value,
                context_structure_kind=event.structure_kind.value,
                context_stop=event.stop_reference,
                context_target=target,
                reference_price=reference_price,
            )
            return
        previous = self._active_context_event
        if previous is not None and previous.event_id != event.event_id:
            self._inc("context_event_superseded_by_later_transition")
            self._trace(
                "context_event_superseded_by_later_transition",
                confirmed_time_ns,
                event_id=previous.event_id,
                context_side=previous.side.name,
                replacement_event_id=event.event_id,
                replacement_side=event.side.name,
            )
        self._active_context_event = event
        self._active_context_confirmed_time_ns = confirmed_time_ns
        self._active_context_basis = (
            f"LIVE_1H_EVENT:{event.path.value}:{event.structure_kind.value}:{event.event_id}"
        )
        self._inc(reason)
        self._trace(
            reason,
            confirmed_time_ns,
            event_id=event.event_id,
            context_side=event.side.name,
            context_path=event.path.value,
            context_structure_kind=event.structure_kind.value,
            context_stop=event.stop_reference,
            context_target=target,
            reference_price=reference_price,
        )

    def _update_context_lifecycle(self, bar: Candle) -> None:
        event = self._active_context_event
        confirmed_time = self._active_context_confirmed_time_ns
        if event is None or confirmed_time is None or bar.ts_close_ns <= confirmed_time:
            return
        target = self._context_target(event, bar.ts_close_ns)
        if target is None:
            self._clear_context(
                time_ns=bar.ts_close_ns,
                reason="context_target_unavailable",
            )
            return
        stop_hit = (
            bar.low <= event.stop_reference
            if event.side is Side.LONG
            else bar.high >= event.stop_reference
        )
        target_hit = (
            bar.high >= target
            if event.side is Side.LONG
            else bar.low <= target
        )
        if stop_hit and target_hit:
            self._clear_context(
                time_ns=bar.ts_close_ns,
                reason="context_stop_and_target_touched_same_bar",
                bar_low=bar.low,
                bar_high=bar.high,
            )
        elif stop_hit:
            self._clear_context(
                time_ns=bar.ts_close_ns,
                reason="context_structural_stop_reached",
                bar_low=bar.low,
                bar_high=bar.high,
            )
        elif target_hit:
            self._clear_context(
                time_ns=bar.ts_close_ns,
                reason="context_structural_target_reached",
                bar_low=bar.low,
                bar_high=bar.high,
            )

    def context_state(self) -> tuple[Side | None, str, StructureEvent | None, int | None]:
        event = self._active_context_event
        return (
            None if event is None else event.side,
            self._active_context_basis,
            event,
            self._active_context_confirmed_time_ns,
        )

    def _create_setups(self, events) -> None:
        events = tuple(events)
        start = len(self.setups)
        super()._create_setups(events)
        new_setups = self.setups[start:]
        confirmed_non_fakeout = []
        for setup in new_setups:
            if setup.state is not StructuralSetupState.WAITING_DISPLACEMENT:
                continue
            if setup.event.path is StructurePath.FAKEOUT:
                interaction = self.structure.bars[setup.event.interaction_index]
                level = interaction.high if setup.event.side is Side.LONG else interaction.low
                self._pending_fakeout_confirmation[setup.setup_id] = level
                self._inc("fakeout_reversal_confirmation_required")
                self._trace(
                    "fakeout_reversal_confirmation_required",
                    setup.event.interaction_time_ns,
                    setup,
                    reversal_confirmation_price=level,
                )
            else:
                confirmed_non_fakeout.append(setup.event)

        sides = {event.side for event in confirmed_non_fakeout}
        if len(sides) > 1:
            self._clear_context(
                time_ns=max(event.interaction_time_ns for event in confirmed_non_fakeout),
                reason="context_conflicting_events_same_close",
                conflicting_event_ids=[event.event_id for event in confirmed_non_fakeout],
            )
            self._inc("context_conflicting_events_same_close_observed")
            return
        if confirmed_non_fakeout:
            # Detector order is already semantic: channel, then trendline, then
            # horizontal swing.  One bar/side is one causal episode.
            event = confirmed_non_fakeout[0]
            self._activate_context(
                event,
                confirmed_time_ns=event.interaction_time_ns,
                reference_price=event.reference_close,
                reason="context_structural_event_activated",
            )

    def _resolve_fakeout_confirmations(self, bar: Candle) -> None:
        for setup_id, level in list(self._pending_fakeout_confirmation.items()):
            setup = self._active.get(setup_id)
            if setup is None:
                self._pending_fakeout_confirmation.pop(setup_id, None)
                continue
            if bar.ts_close_ns <= setup.event.interaction_time_ns:
                continue
            self._pending_fakeout_confirmation.pop(setup_id, None)
            if self._invalidated_before_entry(setup, bar):
                self._finish(
                    setup,
                    StructuralSetupState.INVALIDATED,
                    bar,
                    "fakeout_extreme_breached_before_confirmation",
                )
                continue
            if self._target_spent_before_entry(setup, bar):
                self._finish(
                    setup,
                    StructuralSetupState.TARGET_SPENT,
                    bar,
                    "fakeout_target_spent_during_confirmation",
                )
                continue
            confirmed = (
                bar.close > level
                if setup.event.side is Side.LONG
                else bar.close < level
            )
            if confirmed:
                self._inc("fakeout_context_reversal_confirmed")
                self._trace(
                    "fakeout_context_reversal_confirmed",
                    bar.ts_close_ns,
                    setup,
                    reversal_confirmation_price=level,
                    confirmation_close=bar.close,
                )
                self._activate_context(
                    setup.event,
                    confirmed_time_ns=bar.ts_close_ns,
                    reference_price=bar.close,
                    reason="context_confirmed_fakeout_activated",
                )
            else:
                self._finish(
                    setup,
                    StructuralSetupState.INVALIDATED,
                    bar,
                    "fakeout_next_context_bar_failed_reversal",
                    reversal_confirmation_price=level,
                    confirmation_close=bar.close,
                )

    def _advance(
        self,
        bar: Candle,
        index: int,
        created: Iterable[PriceZone],
    ) -> list[MTFTradePlan]:
        created_zones = tuple(created)
        output: list[MTFTradePlan] = []
        for setup in list(self._active.values()):
            if setup.setup_id in self._pending_fakeout_confirmation:
                # The event remains invalidated immediately if its sweep extreme
                # or objective is crossed before the next context close.
                if bar.ts_close_ns > setup.event.interaction_time_ns:
                    if self._invalidated_before_entry(setup, bar):
                        self._pending_fakeout_confirmation.pop(setup.setup_id, None)
                        self._finish(
                            setup,
                            StructuralSetupState.INVALIDATED,
                            bar,
                            "fakeout_extreme_breached_before_confirmation",
                        )
                    elif self._target_spent_before_entry(setup, bar):
                        self._pending_fakeout_confirmation.pop(setup.setup_id, None)
                        self._finish(
                            setup,
                            StructuralSetupState.TARGET_SPENT,
                            bar,
                            "fakeout_target_spent_during_confirmation",
                        )
                continue
            if (
                setup.state is StructuralSetupState.WAITING_DISPLACEMENT
                and bar.ts_close_ns >= setup.event.interaction_time_ns
            ):
                self._arm_displacement(setup, bar, index, created_zones)
            # Same-bar evidence may arm a setup, never enter it.
            if bar.ts_close_ns <= setup.event.interaction_time_ns:
                continue
            if self._invalidated_before_entry(setup, bar):
                self._finish(
                    setup,
                    StructuralSetupState.INVALIDATED,
                    bar,
                    "structural_stop_breached_before_entry",
                )
                continue
            if self._target_spent_before_entry(setup, bar):
                self._finish(
                    setup,
                    StructuralSetupState.TARGET_SPENT,
                    bar,
                    "structural_target_spent_before_entry",
                )
                continue
            if setup.state is StructuralSetupState.WAITING_DISPLACEMENT:
                continue
            if setup.state is not StructuralSetupState.WAITING_RETEST:
                continue
            if setup.trigger_armed_index is None or index <= setup.trigger_armed_index:
                continue
            live = [zone for zone in setup.trigger_zones if zone.active]
            if not live:
                self._finish(
                    setup,
                    StructuralSetupState.INVALIDATED,
                    bar,
                    "trigger_footprint_invalidated_before_retest",
                )
                continue
            touched = [
                zone
                for zone in live
                if bar.low <= zone.upper and bar.high >= zone.lower
            ]
            if not touched:
                continue
            trigger = min(touched, key=lambda zone: (zone.observed_time_ns, zone.zone_id))
            reacted = (
                bar.close > trigger.upper and bar.close > bar.open
                if setup.event.side is Side.LONG
                else bar.close < trigger.lower and bar.close < bar.open
            )
            if not reacted:
                self._finish(
                    setup,
                    StructuralSetupState.FIRST_RETEST_UNRESOLVED,
                    bar,
                    "first_retest_failed_reaction",
                    trigger_zone_id=trigger.zone_id,
                )
                continue
            plan = self._plan(setup, trigger, bar)
            if plan is not None:
                output.append(plan)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        if timeframe_minutes == self.context_minutes:
            self._update_context_lifecycle(bar)
            self._resolve_fakeout_confirmations(bar)
        return super().on_bar(timeframe_minutes, bar)


class ResearchScenarioBundleV4(_BaseResearchScenarioBundleV4):
    """The same policy at macro and micro scales, with causal event-state routing."""

    TOP_DOWN_SOURCE_RULE = "SOURCE_EXPLICIT:HIGHER_TIMEFRAME_CONTEXT_PRECEDES_LOWER_ENTRY"
    TOP_DOWN_TRANSLATION_RULES = (
        "HUMAN_NATURAL_INFERENCE:LATEST_LIVE_CONFIRMED_1H_STRUCTURAL_EVENT_DEFINES_MEDIUM_STATE",
        "HUMAN_NATURAL_INFERENCE:1H_CONTEXT_PERSISTS_UNTIL_ITS_STRUCTURAL_STOP_OR_OBJECTIVE",
        "HUMAN_NATURAL_INFERENCE:UNRESOLVED_HIGHER_EVENT_CONTEXT_MEANS_NO_MICRO_TRADE",
    )

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        self.symbol = symbol
        self.macro = CausalStructuralScenarioEngine(
            symbol,
            tick_size,
            scale_name="MACRO",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.micro = CausalStructuralScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            context_minutes=15,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.detectors = _EvidenceDetectorView(
            {
                60: self.macro.structure,
                15: self.micro.structure,
                5: self.macro.trigger_detector,
            },
            (self.micro.trigger_detector,),
        )
        self._claimed_episodes = set()
        self._bundle_trace = []
        self._routing_diagnostics: dict[str, int] = {}
        self._last_context_key: tuple[str | None, str] | None = None

    def _route_inc(self, key: str) -> None:
        self._routing_diagnostics[key] = self._routing_diagnostics.get(key, 0) + 1

    @property
    def diagnostics(self):
        output = super().diagnostics
        return {**output, "top_down_router": dict(self._routing_diagnostics)}

    def _higher_context_side(self) -> tuple[Side | None, str]:
        side, basis, _event, _confirmed_time = self.macro.context_state()
        return side, basis

    def _micro_permission(self, side: Side) -> tuple[bool, Side | None, str]:
        higher_side, basis = self._higher_context_side()
        return higher_side is side, higher_side, basis

    def _record_context_change(self, event_time_ns: int) -> None:
        side, basis = self._higher_context_side()
        key = (None if side is None else side.name, basis)
        if key == self._last_context_key:
            return
        self._last_context_key = key
        self._bundle_trace.append(
            {
                "scenario_kind": "higher_event_context_state_changed",
                "event_time_ns": event_time_ns,
                "scale_name": "ROUTER",
                "higher_timeframe_minutes": 60,
                "decision_timeframe_minutes": 15,
                "trigger_timeframe_minutes": 1,
                "higher_context_side": None if side is None else side.name,
                "higher_context_basis": basis,
            },
        )

    def _route_micro_plans(self, plans: Iterable[MTFTradePlan]) -> list[MTFTradePlan]:
        accepted: list[MTFTradePlan] = []
        for plan in plans:
            allowed, higher_side, basis = self._micro_permission(plan.side)
            if not allowed:
                reason = (
                    "micro_plan_rejected_unresolved_higher_event_context"
                    if higher_side is None
                    else "micro_plan_rejected_opposite_higher_event_context"
                )
                self._route_inc(reason)
                self._bundle_trace.append(
                    {
                        "scenario_kind": reason,
                        "event_time_ns": plan.observed_time_ns,
                        "scale_name": plan.scale_name,
                        "higher_timeframe_minutes": 60,
                        "decision_timeframe_minutes": plan.decision_timeframe_minutes,
                        "trigger_timeframe_minutes": plan.trigger_timeframe_minutes,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "higher_context_side": None if higher_side is None else higher_side.name,
                        "higher_context_basis": basis,
                        "interaction_time_ns": plan.interaction_time_ns,
                    },
                )
                continue
            self._route_inc("micro_plan_aligned_live_higher_event")
            accepted.append(
                replace(
                    plan,
                    source_rule_count=plan.source_rule_count + 1,
                    rule_provenance=(
                        plan.rule_provenance
                        + (self.TOP_DOWN_SOURCE_RULE,)
                        + self.TOP_DOWN_TRANSLATION_RULES
                        + (f"ROUTER_OBSERVED:{basis}:{plan.side.name}",)
                    ),
                ),
            )
        return accepted

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        plans: list[MTFTradePlan] = []
        if timeframe_minutes in (60, 5):
            plans.extend(self.macro.on_bar(timeframe_minutes, bar))
            if timeframe_minutes == 60:
                self._record_context_change(bar.ts_close_ns)
        if timeframe_minutes in (15, 1):
            micro_plans = self.micro.on_bar(timeframe_minutes, bar)
            plans.extend(self._route_micro_plans(micro_plans))

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


__all__ = ["CausalStructuralScenarioEngine", "ResearchScenarioBundleV4"]
