from __future__ import annotations

from dataclasses import dataclass
import unittest

from domain import Side
from mtf_strategy_v4_4h import FourHourEasyChartStrategy
from scenario_runtime_v4_4h import FourHourRoutedResearchBundle

NS_MINUTE = 60_000_000_000


@dataclass(frozen=True)
class _Plan:
    plan_id: str
    side: Side
    scale_name: str = "MICRO"
    observed_time_ns: int = 200
    interaction_time_ns: int = 100
    decision_timeframe_minutes: int = 15
    trigger_timeframe_minutes: int = 1
    source_rule_count: int = 1
    rule_provenance: tuple[str, ...] = ()


class FourHourRouterTests(unittest.TestCase):
    def test_bundle_uses_four_hour_context_and_one_hour_retest_stream(self) -> None:
        bundle = FourHourRoutedResearchBundle("TEST", 0.1)
        self.assertEqual(bundle.super_context.context_minutes, 240)
        self.assertEqual(bundle.super_context.trigger_minutes, 60)
        self.assertEqual(bundle.macro.context_minutes, 60)
        self.assertEqual(bundle.micro.context_minutes, 15)

    def test_unresolved_four_hour_event_rejects_lower_plan(self) -> None:
        bundle = FourHourRoutedResearchBundle("TEST", 0.1)
        output = bundle._route_by_super([_Plan("P1", Side.LONG)])
        self.assertEqual(output, [])
        self.assertEqual(
            bundle.diagnostics["top_down_router"].get(
                "plan_rejected_unresolved_4h_event_context",
            ),
            1,
        )

    def test_opposite_four_hour_event_rejects_lower_plan(self) -> None:
        bundle = FourHourRoutedResearchBundle("TEST", 0.1)
        bundle._super_context_side = lambda: (
            Side.SHORT,
            "LIVE_4H_EVENT:FAKEOUT:SWING_HIGH:1",
        )
        output = bundle._route_by_super([_Plan("P1", Side.LONG)])
        self.assertEqual(output, [])
        self.assertEqual(
            bundle.diagnostics["top_down_router"].get(
                "plan_rejected_opposite_4h_event_context",
            ),
            1,
        )

    def test_aligned_four_hour_event_adds_auditable_provenance(self) -> None:
        bundle = FourHourRoutedResearchBundle("TEST", 0.1)
        bundle._super_context_side = lambda: (
            Side.LONG,
            "LIVE_4H_EVENT:FAKEOUT:SWING_LOW:1",
        )
        output = bundle._route_by_super([_Plan("P1", Side.LONG)])
        self.assertEqual(len(output), 1)
        plan = output[0]
        self.assertGreater(plan.source_rule_count, 1)
        self.assertTrue(
            any(
                item.startswith("SUPER_ROUTER_OBSERVED:LIVE_4H_EVENT")
                for item in plan.rule_provenance
            ),
        )

    def test_four_hour_close_bucket_contains_five_bars_per_symbol(self) -> None:
        four_hour_close = 240 * NS_MINUTE
        one_hour_close = 60 * NS_MINUTE
        fifteen_minute_close = 15 * NS_MINUTE
        self.assertEqual(
            FourHourEasyChartStrategy.expected_composite_count(
                four_hour_close,
                4,
            ),
            20,
        )
        self.assertEqual(
            FourHourEasyChartStrategy.expected_composite_count(
                one_hour_close,
                4,
            ),
            16,
        )
        self.assertEqual(
            FourHourEasyChartStrategy.expected_composite_count(
                fifteen_minute_close,
                4,
            ),
            12,
        )


if __name__ == "__main__":
    unittest.main()
