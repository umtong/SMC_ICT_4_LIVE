"""Human-entry EasyChart RE1 policy with one immutable full-position plan.

The prior response-confirmed candidate waited for a lower-frame footprint to
form, detach, return and then survive another completed candle before entry.
That is appropriate for an S/R flip or an FVG mitigation, but it translated a
strong engulfing order block at the planned structure into an unnecessarily
late trade.  The supplied live examples repeatedly enter when the lower-frame
engulfing OB itself completes at the higher-frame location.

This policy therefore assigns different entry responsibilities:

* a high-quality event-local order block whose source candle touches the
  decision area and whose engulfing candle closes away may enter immediately
  at that completed close;
* a weaker OB or an FVG keeps the detached-retest and first-response path;
* accepted breaks still require the full hold/retest/response sequence;
* channel rejection is available only in the ordered four-point phase;
* stop and target remain the natural five-minute invalidation and first
  meaningful objective fixed before the single full-position entry.

No fitted score, ATR threshold, time window, partial position, stop ratchet,
daily rule or trade-count limit is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, SetupState, V5TradePlan
from domain import Candle, Side
from easychart_re1_complete_policy import (
    DecisionAreaEngine,
    EasyChartRE1CompletePolicyBundle,
    SourceFootprintLocatedMixin,
)
from easychart_re1_natural_geometry import (
    EasyChartRE1NaturalGeometryBundle,
    NaturalHorizontalEngine,
    NaturalMajorSwingEngine,
    NaturalMicroEngine,
)
from easychart_re1_phase import ChannelPhaseStructureBook
from easychart_zones import PriceZone, ZoneKind, ZoneSide


IMMEDIATE_STRONG_OB_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "HIGH_QUALITY_ENGULFING_ORDER_BLOCK_AT_THE_PLANNED_STRUCTURE_ENTERS_ON_ITS_COMPLETED_IMPULSE_CLOSE"
)
CHANNEL_PHASE_EXECUTION_RULE = (
    "SOURCE_EXPLICIT:"
    "CHANNEL_REVERSAL_ENTRY_USES_THE_ORDERED_OPPOSITE_FOURTH_POINT_NOT_A_REPEAT_OF_THE_THIRD_POINT"
)
if IMMEDIATE_STRONG_OB_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (IMMEDIATE_STRONG_OB_RULE,)
if CHANNEL_PHASE_EXECUTION_RULE not in _contracts.SOURCE_RULES:
    _contracts.SOURCE_RULES += (CHANNEL_PHASE_EXECUTION_RULE,)


class ImmediateStrongOrderBlockMixin:
    """Enter a strong OB at its close; retain the inherited retest path otherwise."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._immediate_plans: list[V5TradePlan] = []
        self._immediate_counts: dict[str, int] = {}

    def _iinc(self, key: str) -> None:
        self._immediate_counts[key] = self._immediate_counts.get(key, 0) + 1

    @staticmethod
    def _wanted_side(setup: ScenarioSetup) -> ZoneSide:
        return ZoneSide.SUPPORT if setup.side is Side.LONG else ZoneSide.RESISTANCE

    def _direct_candidates(
        self,
        setup: ScenarioSetup,
        created: list[PriceZone],
    ) -> list[PriceZone]:
        if setup.confirmation_time_ns is None:
            return []
        wanted = self._wanted_side(setup)
        return [
            zone
            for zone in created
            if zone.kind is ZoneKind.ORDER_BLOCK
            and zone.side is wanted
            and zone.high_quality_by_size
            and zone.observed_time_ns > setup.confirmation_time_ns
            and self._formation_touches_context(zone, setup)
        ]

    def _arm_displacements(
        self,
        bar: Candle,
        index: int,
        created: list[PriceZone],
    ) -> None:
        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_DISPLACEMENT:
                continue
            if setup.confirmation_time_ns is None or bar.ts_close_ns <= setup.confirmation_time_ns:
                continue
            if self._target_is_spent(setup, bar):
                self._finish(setup, SetupState.TARGET_SPENT, bar.ts_close_ns, "target_spent_before_immediate_ob_entry")
                continue
            if self._extreme_breached(setup, bar):
                self._finish(
                    setup,
                    SetupState.INVALIDATED,
                    bar.ts_close_ns,
                    "interaction_extreme_breached_before_immediate_ob_entry",
                )
                continue

            trigger = self._select_footprint(self._direct_candidates(setup, created), setup)
            if trigger is None:
                continue
            setup.trigger_zone = trigger
            setup.trigger_index = index
            self._audit(trigger)
            self._inc("event_local_strong_order_block_immediate")
            if setup.side is Side.LONG:
                stop = min(setup.interaction_extreme - self.tick_size, trigger.invalidation)
            else:
                stop = max(setup.interaction_extreme + self.tick_size, trigger.invalidation)
            plan = self._make_plan(
                setup,
                bar,
                entry=bar.close,
                stop=stop,
                trigger_zone=trigger,
                trigger_kind=trigger.kind,
                trigger_strength=trigger.strength_ratio,
            )
            if plan is None:
                self._iinc("immediate_strong_ob_geometry_rejected")
                continue
            self._immediate_plans.append(plan)
            self._iinc("immediate_strong_ob_plan_created")
            self._trace(
                "immediate_strong_ob_plan_created",
                bar.ts_close_ns,
                setup,
                plan_id=plan.plan_id,
                trigger_zone_id=trigger.zone_id,
                trigger_strength_ratio=trigger.strength_ratio,
                entry=plan.entry,
                stop=plan.stop,
                target=plan.target,
                gross_rr=plan.gross_rr,
                rule_provenance=IMMEDIATE_STRONG_OB_RULE,
            )

        # Weak OBs and qualified FVGs keep the inherited departure/retest path.
        super()._arm_displacements(bar, index, created)

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes != self.trigger_minutes:
            return super().on_bar(timeframe_minutes, bar)
        self._immediate_plans = []
        delayed = super().on_bar(timeframe_minutes, bar)
        output = self._immediate_plans + delayed
        return sorted(
            output,
            key=lambda plan: (
                plan.interaction_time_ns,
                plan.observed_time_ns,
                plan.plan_id,
            ),
        )

    @property
    def immediate_entry_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._immediate_counts.items())),
            "rule_provenance": IMMEDIATE_STRONG_OB_RULE,
        }


class HumanMicroEngine(
    ImmediateStrongOrderBlockMixin,
    SourceFootprintLocatedMixin,
    NaturalMicroEngine,
):
    """Ordered channel phase plus direct strong-OB rejection entry."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = ChannelPhaseStructureBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )


class HumanHorizontalEngine(
    ImmediateStrongOrderBlockMixin,
    SourceFootprintLocatedMixin,
    NaturalHorizontalEngine,
):
    pass


class HumanMajorSwingEngine(
    ImmediateStrongOrderBlockMixin,
    SourceFootprintLocatedMixin,
    NaturalMajorSwingEngine,
):
    pass


class HumanDecisionAreaEngine(
    ImmediateStrongOrderBlockMixin,
    DecisionAreaEngine,
):
    pass


class EasyChartRE1HumanPolicyBundle(EasyChartRE1CompletePolicyBundle):
    """Complete mechanism router with source-like immediate engulfing entry."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = HumanMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.horizontal = HumanHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.major_swing = HumanMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.decision_area = HumanDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="DECISION_AREA_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        for key in ("micro", "horizontal", "major_swing", "decision_area", "horizontal_flip"):
            self._audit_offsets[key] = 0

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        # Explicit OB decisions and horizontal S/R flips claim their own price
        # episode before the generic diagonal engine can label the same event.
        special: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
            decision_raw = self.decision_area.on_bar(timeframe_minutes, bar)
            flip_raw = self.horizontal_flip.on_bar(timeframe_minutes, bar)
            self._sync_audit("decision_area", self.decision_area)
            self._sync_audit("horizontal_flip", self.horizontal_flip)
            special.extend(self._route_decision_area(decision_raw))
            special.extend(self._route_horizontal_flip(flip_raw))

        routed = EasyChartRE1NaturalGeometryBundle.on_bar(self, timeframe_minutes, bar)
        return sorted(
            special + routed,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["human_entry_policy"] = {
            "micro": self.micro.immediate_entry_diagnostics,
            "horizontal": self.horizontal.immediate_entry_diagnostics,
            "major_swing": self.major_swing.immediate_entry_diagnostics,
            "decision_area": self.decision_area.immediate_entry_diagnostics,
            "channel_phase": self.micro.structure.phase_diagnostics,
            "rules": (IMMEDIATE_STRONG_OB_RULE, CHANNEL_PHASE_EXECUTION_RULE),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1HumanPolicyBundle
