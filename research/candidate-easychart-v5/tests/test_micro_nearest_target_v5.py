from __future__ import annotations

import unittest

from domain import Candle
from scenario_micro_nearest_target_v5 import (
    MicroNearestAnyTargetResearchScenarioBundleV5,
)

NS = 60_000_000_000


def candle(index: int) -> Candle:
    return Candle(index * NS, 100.0, 101.0, 99.0, 100.5, 1.0)


class MicroNearestTargetBundleTests(unittest.TestCase):
    def test_macro_engine_does_not_compete_for_the_global_slot(self) -> None:
        bundle = MicroNearestAnyTargetResearchScenarioBundleV5("TEST", 0.1)
        bundle.on_bar(60, candle(1))
        bundle.on_bar(15, candle(2))
        bundle.on_bar(5, candle(3))
        bundle.on_bar(1, candle(4))

        self.assertEqual(len(bundle.detectors[60].bars), 1)
        self.assertEqual(len(bundle.macro.structure.bars), 0)
        self.assertEqual(len(bundle.macro.decision_bars), 0)
        self.assertEqual(len(bundle.macro.trigger_detector.bars), 0)

        self.assertEqual(len(bundle.micro.structure.bars), 1)
        self.assertEqual(len(bundle.micro.decision_bars), 1)
        self.assertEqual(len(bundle.micro.trigger_detector.bars), 1)
        policy = bundle.diagnostics["scale_policy"]
        self.assertFalse(policy["macro_order_family_enabled"])
        self.assertTrue(policy["micro_order_family_enabled"])

    def test_target_policy_remains_nearest_any(self) -> None:
        bundle = MicroNearestAnyTargetResearchScenarioBundleV5("TEST", 0.1)
        policy = bundle.diagnostics["target_policy"]
        self.assertEqual(
            policy["name"],
            "NEAREST_ANY_CONFIRMED_PREEXISTING_OPPOSITE_PIVOT",
        )


if __name__ == "__main__":
    unittest.main()
