"""Latest-live 4h structural-event router for the EasyChart v4 system.

EasyChart instructs traders to draw the large channel first and use smaller
charts for entry.  The source also says higher-timeframe channels are more
reliable, while 1m/5m structures are noisy and short-lived.  This module makes
that human top-down action explicit without introducing a moving average,
calendar regime label or optimized slope threshold.

The 4h layer uses the exact same causal grammar as the lower layers.  A 1h or
15m plan is executable only while a fully confirmed, still-live 4h structural
event points the same way.  An accepted 4h break is unresolved until its first
later 1h S/R-flip retest holds.  The state ends at its own structural stop or
objective.  Unresolved or opposite 4h context means no lower trade.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

from domain import Candle, Side
from easychart_mtf_scenario import MTFTradePlan
from easychart_zones import PriceZone
from market_structure import StructuralBoundary
from scenario_bundle_v4 import StructuralSetup, _EvidenceDetectorView
from scenario_runtime_v4_acceptance_gate import (
    SourceFaithfulRetestEntryGatedBundle,
    SourceFaithfulRetestEntryGatedEngine,
)


class FourHourRoutedResearchBundle(SourceFaithfulRetestEntryGatedBundle):
    """One live 4h scene routes the existing 1h and 15m scenario families."""

    SUPER_SOURCE_RULES = (
        "SOURCE_EXPLICIT:LARGE_HIGHER_TIMEFRAME_CHANNEL_IS_DRAWN_BEFORE_SMALL_ENTRY_CHANNEL",
        "SOURCE_EXPLICIT:HIGHER_TIMEFRAME_CHANNELS_HAVE_STRONGER_SUPPORT_AND_RESISTANCE",
    )
    SUPER_TRANSLATION_RULES = (
        "HUMAN_NATURAL_INFERENCE:LATEST_LIVE_CONFIRMED_4H_STRUCTURAL_EVENT_DEFINES_THE_LARGE_SCENE",
        "HUMAN_NATURAL_INFERENCE:4H_CONTEXT_PERSISTS_ONLY_UNTIL_ITS_OWN_STRUCTURAL_STOP_OR_OBJECTIVE",
        "HUMAN_NATURAL_INFERENCE:UNRESOLVED_OR_OPPOSITE_4H_EVENT_CONTEXT_REJECTS_LOWER_PLANS",
    )

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.super_context = SourceFaithfulRetestEntryGatedEngine(
            symbol,
            tick_size,
            scale_name="SUPER",
            context_minutes=240,
            trigger_minutes=60,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.detectors = _EvidenceDetectorView(
            {
                240: self.super_context.structure,
                60: self.macro.structure,
                15: self.micro.structure,
                5: self.macro.trigger_detector,
            },
            (
                self.micro.trigger_detector,
                self.super_context.trigger_detector,
            ),
        )
        self._last_super_context_key: tuple[str | None, str] | None = None

    @property
    def setups(self) -> list[StructuralSetup]:
        return self.super_context.setups + self.macro.setups + self.micro.setups

    @property
    def plans(self) -> list[MTFTradePlan]:
        return self.super_context.plans + self.macro.plans + self.micro.plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "super": self.super_context.diagnostics,
            "macro": self.macro.diagnostics,
            "micro": self.micro.diagnostics,
            "super_structure": self.super_context.structure.diagnostics,
            "macro_structure": self.macro.structure.diagnostics,
            "micro_structure": self.micro.structure.diagnostics,
            "top_down_router": dict(self._routing_diagnostics),
        }

    def _super_context_side(self) -> tuple[Side | None, str]:
        side, basis, _event, _confirmed_time = self.super_context.context_state()
        return side, basis

    def _record_super_context_change(self, event_time_ns: int) -> None:
        side, basis = self._super_context_side()
        key = (None if side is None else side.name, basis)
        if key == self._last_super_context_key:
            return
        self._last_super_context_key = key
        self._bundle_trace.append(
            {
                "scenario_kind": "super_context_state_changed",
                "event_time_ns": event_time_ns,
                "scale_name": "SUPER_ROUTER",
                "higher_timeframe_minutes": 240,
                "decision_timeframe_minutes": 60,
                "trigger_timeframe_minutes": 15,
                "super_context_side": None if side is None else side.name,
                "super_context_basis": basis,
            },
        )

    def _route_by_super(
        self,
        plans: Iterable[MTFTradePlan],
    ) -> list[MTFTradePlan]:
        accepted: list[MTFTradePlan] = []
        super_side, basis = self._super_context_side()
        source_count = sum(
            item.startswith("SOURCE_EXPLICIT:")
            for item in self.SUPER_SOURCE_RULES
        )
        for plan in plans:
            if super_side is not plan.side:
                reason = (
                    "plan_rejected_unresolved_4h_event_context"
                    if super_side is None
                    else "plan_rejected_opposite_4h_event_context"
                )
                self._route_inc(reason)
                self._bundle_trace.append(
                    {
                        "scenario_kind": reason,
                        "event_time_ns": plan.observed_time_ns,
                        "scale_name": plan.scale_name,
                        "higher_timeframe_minutes": 240,
                        "decision_timeframe_minutes": plan.decision_timeframe_minutes,
                        "trigger_timeframe_minutes": plan.trigger_timeframe_minutes,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "super_context_side": (
                            None if super_side is None else super_side.name
                        ),
                        "super_context_basis": basis,
                        "interaction_time_ns": plan.interaction_time_ns,
                    },
                )
                continue
            self._route_inc("plan_aligned_live_4h_event")
            accepted.append(
                replace(
                    plan,
                    source_rule_count=plan.source_rule_count + source_count,
                    rule_provenance=(
                        plan.rule_provenance
                        + self.SUPER_SOURCE_RULES
                        + self.SUPER_TRANSLATION_RULES
                        + (f"SUPER_ROUTER_OBSERVED:{basis}:{plan.side.name}",)
                    ),
                ),
            )
        return accepted

    def _deduplicate(self, plans: Iterable[MTFTradePlan]) -> list[MTFTradePlan]:
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

        if timeframe_minutes in (240, 60):
            # SUPER plans are retained as state evidence only.  The 60m bar is
            # its lower-timeframe structural-retest/footprint stream.
            self.super_context.on_bar(timeframe_minutes, bar)
            self._record_super_context_change(bar.ts_close_ns)

        if timeframe_minutes in (60, 5):
            macro_plans = self.macro.on_bar(timeframe_minutes, bar)
            plans.extend(self._route_by_super(macro_plans))
            if timeframe_minutes == 60:
                self._record_context_change(bar.ts_close_ns)

        if timeframe_minutes in (15, 1):
            micro_plans = self._route_micro_plans(
                self.micro.on_bar(timeframe_minutes, bar),
            )
            plans.extend(self._route_by_super(micro_plans))

        return self._deduplicate(plans)

    def drain_trace(self) -> list[dict[str, Any]]:
        output = (
            self.super_context.drain_trace()
            + self.macro.drain_trace()
            + self.micro.drain_trace()
            + self._bundle_trace
        )
        self._bundle_trace = []
        return output

    def find_zone(self, zone_id: str) -> StructuralBoundary | PriceZone | None:
        return (
            self.super_context.find_zone(zone_id)
            or self.macro.find_zone(zone_id)
            or self.micro.find_zone(zone_id)
        )


__all__ = ["FourHourRoutedResearchBundle"]
