"""Single-family volume-clock flow core for EasyChart RE1.

The broad state system restored frequency but allowed weak visual entries to
occupy the one global account slot. The first volume-clock diagnostic exposed a
cleaner responsibility split: micro trend-line/channel structure plus
volume-synchronized absorption/initiative was the only mechanism with positive
R contribution in both short development periods, while ordinary visual plans
and unrelated structure families supplied most losses.

This candidate is not a family-performance filter layered after plan creation.
It is a smaller complete decision policy:

* 60m/15m price structure still defines market state;
* the 15m diagonal/channel book supplies the decision boundary;
* one first typical-volume bucket supplies absorption or accepted-break
  initiative;
* natural 5m/15m invalidation and first obstacle remain unchanged;
* no OB/FVG-only plan and no other family may reserve or claim the account slot;
* a channel-edge fade cannot use absorption to fight an established 60m move
  unless the plan is itself located at a same-side 60m decision area. A
  trend-line fakeout remains a distinct stop-sweep reversal mechanism.

Removing the unrelated families lets additional independent micro auctions reach
the account router, so selectivity need not mean lower realized frequency.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle
from easychart_re1_flow_volume_clock import EasyChartRE1VolumeClockFlowBundle


MICRO_FLOW_CORE_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "ONE_COMPLETE_SYSTEM_USES_CAUSAL_MARKET_STATE_DIAGONAL_LOCATION_AND_VOLUME_CLOCK_FLOW_AS_THE_MICRO_AUCTION_ENTRY_CORE"
)
CHANNEL_FADE_MACRO_ALIGNMENT_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "CHANNEL_EDGE_REJECTION_IS_A_RANGE_FADE_AND_CANNOT_FIGHT_ESTABLISHED_SIXTY_MINUTE_DIRECTION_WITHOUT_SAME_SIDE_HTF_DECISION_AREA"
)
if MICRO_FLOW_CORE_RULE not in _contracts.RESEARCH_RULES:
    _contracts.RESEARCH_RULES += (MICRO_FLOW_CORE_RULE,)
if CHANNEL_FADE_MACRO_ALIGNMENT_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (CHANNEL_FADE_MACRO_ALIGNMENT_RULE,)


class EasyChartRE1VolumeClockMicroCoreBundle(EasyChartRE1VolumeClockFlowBundle):
    """Only flow-triggered MICRO plans can reach the continuous account."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._micro_core_counts: dict[str, int] = {}
        self._micro_core_trace: list[dict[str, Any]] = []

    def _mcinc(self, key: str) -> None:
        self._micro_core_counts[key] = self._micro_core_counts.get(key, 0) + 1

    def _countertrend_channel_fade_without_htf_area(self, plan: V5TradePlan) -> bool:
        if (
            plan.scenario_path != ScenarioPath.REJECTION.value
            or self._macro_side is None
            or plan.side is self._macro_side
        ):
            return False
        kind = str(getattr(plan.higher_zone_kind, "value", plan.higher_zone_kind))
        if "CHANNEL" not in kind:
            return False
        # The pre-existing 60m structure/OB/FVG evidence is the only exception.
        evidence = self._structure_evidence(plan) + self._footprint_evidence(plan)
        return not evidence

    def _route_plan(self, plan: V5TradePlan) -> bool:
        if plan.scale_name != "MICRO":
            self._mcinc("non_micro_plan_suppressed")
            return False
        if not self._flow_plan(plan):
            self._mcinc("micro_visual_only_plan_suppressed")
            self._micro_core_trace.append(
                {
                    "scenario_kind": "micro_visual_only_plan_suppressed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "scenario_path": plan.scenario_path,
                    "trigger_zone_kind": self._trigger_kind(plan),
                    "rule_provenance": MICRO_FLOW_CORE_RULE,
                },
            )
            return False
        if self._countertrend_channel_fade_without_htf_area(plan):
            self._mcinc("countertrend_channel_fade_without_htf_area_suppressed")
            self._micro_core_trace.append(
                {
                    "scenario_kind": "countertrend_channel_fade_without_htf_area_suppressed",
                    "event_time_ns": plan.observed_time_ns,
                    "symbol": plan.symbol,
                    "plan_id": plan.plan_id,
                    "side": plan.side.name,
                    "macro_side": self._macro_side.name,
                    "scenario_path": plan.scenario_path,
                    "higher_zone_kind": str(
                        getattr(plan.higher_zone_kind, "value", plan.higher_zone_kind)
                    ),
                    "trigger_zone_kind": self._trigger_kind(plan),
                    "rule_provenance": CHANNEL_FADE_MACRO_ALIGNMENT_RULE,
                },
            )
            return False
        allowed = super()._route_plan(plan)
        self._mcinc(
            "micro_flow_plan_allowed"
            if allowed
            else "micro_flow_plan_rejected_by_context"
        )
        return allowed

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        # Retain complete closed-bar evidence even though the broad family router
        # is intentionally bypassed. This does not participate in decisions.
        if timeframe_minutes in self.detectors:
            self.detectors[timeframe_minutes].on_bar(bar)

        # Preserve the causal market-state books without invoking the broad
        # multi-family plan router.
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
                self._mcinc("micro_flow_duplicate_episode_suppressed")
                continue
            if not self._route_plan(plan):
                continue
            # Claim only after all mechanism and context routing has accepted
            # the plan. Suppressed families can never reserve a later episode.
            self._claim_episode(plan)
            output.append(plan)
        return output

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self._micro_core_trace
        self._micro_core_trace = []
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["volume_clock_micro_flow_core"] = {
            "counts": dict(sorted(self._micro_core_counts.items())),
            "rules": (
                MICRO_FLOW_CORE_RULE,
                CHANNEL_FADE_MACRO_ALIGNMENT_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1VolumeClockMicroCoreBundle
