"""Micro-only EasyChart v5 with the nearest-any-pivot objective policy.

This diagnostic combines the two strongest development findings without adding
a threshold or score: macro order families are removed as slot competitors,
and the source-unsupported target-span gate is removed.  All micro scenarios,
causal state transitions, risk sizing, execution and one-slot arbitration remain
unchanged.
"""
from __future__ import annotations

from typing import Any

from domain import Candle
from contracts_v5 import V5TradePlan
from scenario_target_ablation_v5 import NearestAnyTargetResearchScenarioBundleV5


class MicroNearestAnyTargetResearchScenarioBundleV5(
    NearestAnyTargetResearchScenarioBundleV5,
):
    """Run only 15/5/1 decisions while preserving every audit timeframe."""

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["scale_policy"] = {
            "macro_order_family_enabled": False,
            "micro_order_family_enabled": True,
            "macro_role": "DEFERRED_CONTEXT_ROUTER_ONLY",
        }
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes in self.detectors:
            self.detectors[timeframe_minutes].on_bar(bar)

        plans: list[V5TradePlan] = []
        if timeframe_minutes in {15, 5, 1}:
            plans.extend(self.micro.on_bar(timeframe_minutes, bar))
        self._sync_audit("micro", self.micro)

        ranked = sorted(
            plans,
            key=lambda plan: (
                plan.interaction_time_ns,
                -plan.higher_timeframe_minutes,
                plan.symbol,
                plan.plan_id,
            ),
        )
        independent: list[V5TradePlan] = []
        for plan in ranked:
            if self._duplicate_episode(plan):
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
            self._claim_episode(plan)
            independent.append(plan)
        return independent
