from __future__ import annotations

from dataclasses import dataclass
import unittest

from domain import Side
from scenario_runtime_v4_meso import MesoResearchBundle


@dataclass(frozen=True)
class _Plan:
    plan_id: str
    side: Side
    scale_name: str
    interaction_time_ns: int
    observed_time_ns: int
    higher_timeframe_minutes: int
    decision_timeframe_minutes: int
    trigger_timeframe_minutes: int
    source_rule_count: int = 1
    rule_provenance: tuple[str, ...] = ()


class MesoBundleTests(unittest.TestCase):
    def test_meso_reuses_same_policy_at_five_to_one_minutes(self) -> None:
        bundle = MesoResearchBundle("TEST", 0.1)
        self.assertEqual(bundle.meso.context_minutes, 5)
        self.assertEqual(bundle.meso.trigger_minutes, 1)
        self.assertEqual(bundle.meso.scale_name, "MESO")
        self.assertIn("meso", bundle.diagnostics)
        self.assertIn("meso_structure", bundle.diagnostics)

    def test_aligned_meso_plan_receives_top_down_and_scale_provenance(self) -> None:
        bundle = MesoResearchBundle("TEST", 0.1)
        bundle._micro_permission = lambda side: (True, side, "LIVE_1H_EVENT:TEST")
        source = _Plan(
            plan_id="MESO-1",
            side=Side.LONG,
            scale_name="MESO",
            interaction_time_ns=100,
            observed_time_ns=200,
            higher_timeframe_minutes=5,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        routed = bundle._route_meso_plans([source])
        self.assertEqual(len(routed), 1)
        plan = routed[0]
        self.assertEqual(plan.source_rule_count, 3)
        self.assertIn(bundle.TOP_DOWN_SOURCE_RULE, plan.rule_provenance)
        self.assertIn(bundle.MESO_SOURCE_RULE, plan.rule_provenance)

    def test_same_close_15m_interpretation_precedes_5m_duplicate(self) -> None:
        bundle = MesoResearchBundle("TEST", 0.1)
        micro = _Plan(
            plan_id="MICRO-1",
            side=Side.SHORT,
            scale_name="MICRO",
            interaction_time_ns=100,
            observed_time_ns=210,
            higher_timeframe_minutes=15,
            decision_timeframe_minutes=15,
            trigger_timeframe_minutes=1,
        )
        meso = _Plan(
            plan_id="MESO-1",
            side=Side.SHORT,
            scale_name="MESO",
            interaction_time_ns=100,
            observed_time_ns=200,
            higher_timeframe_minutes=5,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        output = bundle._deduplicate([meso, micro])
        self.assertEqual([item.plan_id for item in output], ["MICRO-1"])

    def test_distinct_five_minute_interactions_remain_independent(self) -> None:
        bundle = MesoResearchBundle("TEST", 0.1)
        first = _Plan(
            plan_id="MESO-1",
            side=Side.LONG,
            scale_name="MESO",
            interaction_time_ns=100,
            observed_time_ns=150,
            higher_timeframe_minutes=5,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        second = _Plan(
            plan_id="MESO-2",
            side=Side.LONG,
            scale_name="MESO",
            interaction_time_ns=200,
            observed_time_ns=250,
            higher_timeframe_minutes=5,
            decision_timeframe_minutes=5,
            trigger_timeframe_minutes=1,
        )
        output = bundle._deduplicate([first, second])
        self.assertEqual([item.plan_id for item in output], ["MESO-1", "MESO-2"])


if __name__ == "__main__":
    unittest.main()
