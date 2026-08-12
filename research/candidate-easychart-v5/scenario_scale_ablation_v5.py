"""Causal scale ablations for EasyChart v5.

These bundles do not add a trading filter or change any scenario rule.  They
remove one complete decision scale at a time so the one-slot, four-symbol
Nautilus account can reveal whether macro and micro policies contribute alpha,
consume each other's opportunities, or merely look different in a post-hoc
trade table.  Only the integrated account result is used for that diagnosis.
"""
from __future__ import annotations

from domain import Candle
from contracts_v5 import V5TradePlan
from scenario_bundle_v5 import ResearchScenarioBundleV5


class _ScaleFilteredResearchScenarioBundleV5(ResearchScenarioBundleV5):
    ENABLED_SCALES: frozenset[str] = frozenset({"macro", "micro"})

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        if not self.ENABLED_SCALES or not self.ENABLED_SCALES <= {"macro", "micro"}:
            raise ValueError("enabled scales must be a non-empty subset of macro/micro")
        self.enabled_scales = self.ENABLED_SCALES

    @property
    def diagnostics(self):  # type: ignore[no-untyped-def]
        output = dict(super().diagnostics)
        output["scale_ablation"] = {
            "macro_enabled": "macro" in self.enabled_scales,
            "micro_enabled": "micro" in self.enabled_scales,
        }
        return output

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        # Preserve every audit timeframe even when its decision engine is
        # disabled.  Trade windows and data availability remain comparable.
        if timeframe_minutes in self.detectors:
            self.detectors[timeframe_minutes].on_bar(bar)

        plans: list[V5TradePlan] = []
        if "macro" in self.enabled_scales and timeframe_minutes in {60, 15, 5}:
            plans.extend(self.macro.on_bar(timeframe_minutes, bar))
        if "micro" in self.enabled_scales and timeframe_minutes in {15, 5, 1}:
            plans.extend(self.micro.on_bar(timeframe_minutes, bar))

        if "macro" in self.enabled_scales:
            self._sync_audit("macro", self.macro)
        if "micro" in self.enabled_scales:
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


class MacroOnlyResearchScenarioBundleV5(_ScaleFilteredResearchScenarioBundleV5):
    ENABLED_SCALES = frozenset({"macro"})


class MicroOnlyResearchScenarioBundleV5(_ScaleFilteredResearchScenarioBundleV5):
    ENABLED_SCALES = frozenset({"micro"})


BUNDLE_BY_SCALE = {
    "full": ResearchScenarioBundleV5,
    "macro": MacroOnlyResearchScenarioBundleV5,
    "micro": MicroOnlyResearchScenarioBundleV5,
}
