"""Independent 5m channel-Fakeout family inside the live 1h EasyChart scene.

EasyChart says low-timeframe channels are noisy and short-lived, while the
highest-information event at a watched channel edge is a liquidity sweep which
closes back inside and reverses rapidly.  The all-path 5m diagnostic confirmed
that simply copying every 15m structure path to 5m adds many low-quality
Bounce, Acceptance and delayed-Trap trades.

This family is not a looser version of those rules.  It is one complete causal
scenario with a distinct mechanism:

    live aligned 1h structural event
    -> confirmed 5m parallel-channel edge Fakeout
    -> source-sized event-local 1m OB/FVG displacement
    -> first later 1m footprint retest with reaction
    -> 5m structural invalidation and opposite-edge/structure objective

All ordinary 15m->1m MICRO families remain unchanged.  No numerical threshold,
time filter, score or post-result ranking is added.
"""
from __future__ import annotations

from easychart_mtf_scenario import MTFTradePlan
from market_structure import StructureKind, StructurePath
from scenario_runtime_v4_meso import MesoResearchBundle


class ChannelFakeoutMesoResearchBundle(MesoResearchBundle):
    """Route only the source-distinct 5m channel liquidity-sweep family."""

    MESO_CHANNEL_FAKEOUT_RULES = (
        "SOURCE_EXPLICIT:LOW_TIMEFRAME_CHANNELS_ARE_NOISY_AND_SHORT_LIVED",
        "SOURCE_EXPLICIT:CHANNEL_EDGE_FAKEOUT_IS_A_LIQUIDITY_SWEEP_CLOSING_BACK_INSIDE",
        "SOURCE_EXPLICIT:FAKEOUT_REVERSES_RAPIDLY_AFTER_RECLAIM",
        "HUMAN_NATURAL_INFERENCE:5M_CHANNEL_IS_USED_ONLY_AFTER_ITS_STRONGEST_FAILED_BREAK_STATE_TRANSITION",
    )

    @staticmethod
    def _is_channel_fakeout(plan: MTFTradePlan) -> bool:
        kind = getattr(plan.higher_zone_kind, "value", str(plan.higher_zone_kind))
        return (
            plan.scenario_path == StructurePath.FAKEOUT.value
            and kind
            in {
                StructureKind.CHANNEL_LOWER.value,
                StructureKind.CHANNEL_UPPER.value,
            }
        )

    def _route_meso_plans(self, plans: list[MTFTradePlan]) -> list[MTFTradePlan]:
        eligible: list[MTFTradePlan] = []
        for plan in plans:
            if not self._is_channel_fakeout(plan):
                self._route_inc("meso_non_channel_fakeout_family_not_routed")
                self._bundle_trace.append(
                    {
                        "scenario_kind": "meso_non_channel_fakeout_family_not_routed",
                        "event_time_ns": plan.observed_time_ns,
                        "scale_name": plan.scale_name,
                        "higher_timeframe_minutes": plan.higher_timeframe_minutes,
                        "decision_timeframe_minutes": plan.decision_timeframe_minutes,
                        "trigger_timeframe_minutes": plan.trigger_timeframe_minutes,
                        "plan_id": plan.plan_id,
                        "side": plan.side.name,
                        "scenario_path": plan.scenario_path,
                        "structure_kind": getattr(
                            plan.higher_zone_kind,
                            "value",
                            str(plan.higher_zone_kind),
                        ),
                        "interaction_time_ns": plan.interaction_time_ns,
                    },
                )
                continue
            eligible.append(plan)
        routed = super()._route_meso_plans(eligible)
        output: list[MTFTradePlan] = []
        from dataclasses import replace

        for plan in routed:
            output.append(
                replace(
                    plan,
                    source_rule_count=(
                        plan.source_rule_count
                        + sum(
                            item.startswith("SOURCE_EXPLICIT:")
                            for item in self.MESO_CHANNEL_FAKEOUT_RULES
                        )
                    ),
                    rule_provenance=(
                        plan.rule_provenance + self.MESO_CHANNEL_FAKEOUT_RULES
                    ),
                ),
            )
        return output


__all__ = ["ChannelFakeoutMesoResearchBundle"]
