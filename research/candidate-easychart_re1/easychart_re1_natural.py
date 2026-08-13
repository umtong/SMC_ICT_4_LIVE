"""Natural scenario routing for EasyChart RE1.

The repeated-defense horizontal detector was introduced to recover the source's
box-range and defended-level examples.  Its first diagnostic showed that the
same geometric level should not originate every possible state-machine path:
blind bounces and accepted breaks were weak and duplicated work already handled
by the channel/trend-line family, while sweep-and-reclaim rejections were the
stable horizontal use case.

This module therefore keeps two independent opportunity families:

* diagonal trend-line/channel scenarios: rejection, rotation and accepted break;
* repeated-defense horizontal scenarios: liquidity sweep followed by reclaim,
  displacement and the first distinct retest.

No score, fitted distance, clock filter, risk multiplier or outcome-dependent
selection is introduced.  The distinction is causal: a repeatedly defended
horizontal level represents a pool of stops, so its executable role is the
Fake-out/Trap rejection path described in the supplied material.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, V5TradePlan
from domain import Candle
from easychart_re1_fresh import EasyChartRE1FreshBundle
from easychart_re1_horizontal import EasyChartRE1IntegratedBundle


HORIZONTAL_SWEEP_RECLAIM_ONLY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "REPEATED_DEFENSE_HORIZONTAL_LEVEL_ORIGINATES_ONLY_SWEEP_RECLAIM_REJECTION"
)
if HORIZONTAL_SWEEP_RECLAIM_ONLY_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (HORIZONTAL_SWEEP_RECLAIM_ONLY_RULE,)


class EasyChartRE1NaturalBundle(EasyChartRE1IntegratedBundle):
    """Diagonal core plus horizontal sweep/reclaim, routed as one plan stream."""

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        # Call the diagonal parent directly.  The integrated parent's on_bar
        # would route every horizontal path before this subclass could reject
        # the paths which do not match the horizontal liquidity mechanism.
        diagonal = EasyChartRE1FreshBundle.on_bar(self, timeframe_minutes, bar)
        if timeframe_minutes not in {15, 5, 1}:
            return diagonal

        horizontal_raw = self.horizontal.on_bar(timeframe_minutes, bar)
        self._sync_audit("horizontal", self.horizontal)
        horizontal: list[V5TradePlan] = []
        for plan in sorted(
            horizontal_raw,
            key=lambda item: (
                item.interaction_time_ns,
                item.observed_time_ns,
                item.plan_id,
            ),
        ):
            if plan.scenario_path != ScenarioPath.REJECTION.value:
                self._horizontal_bundle_trace.append(
                    {
                        "scenario_kind": "horizontal_non_sweep_path_suppressed",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "scenario_path": plan.scenario_path,
                        "interaction_time_ns": plan.interaction_time_ns,
                        "rule_provenance": HORIZONTAL_SWEEP_RECLAIM_ONLY_RULE,
                    },
                )
                continue

            # Diagonal gets precedence only when both engines describe the same
            # side, time and overlapping price episode.  Otherwise the sweep is
            # an independent account candidate.
            if self._duplicate_episode(plan):
                self._horizontal_bundle_trace.append(
                    {
                        "scenario_kind": "horizontal_episode_overlapped_existing_family",
                        "event_time_ns": plan.observed_time_ns,
                        "symbol": plan.symbol,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "interaction_time_ns": plan.interaction_time_ns,
                        "overlap_lower": plan.overlap_lower,
                        "overlap_upper": plan.overlap_upper,
                    },
                )
                continue
            self._claim_episode(plan)
            if self._route_plan(plan):
                horizontal.append(plan)
        return diagonal + horizontal

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        horizontal = dict(output.get("horizontal_family", {}))
        horizontal.update(
            {
                "executable_paths": (ScenarioPath.REJECTION.value,),
                "path_policy": HORIZONTAL_SWEEP_RECLAIM_ONLY_RULE,
            },
        )
        output["horizontal_family"] = horizontal
        return output


MultiScaleScenarioBundle = EasyChartRE1NaturalBundle
