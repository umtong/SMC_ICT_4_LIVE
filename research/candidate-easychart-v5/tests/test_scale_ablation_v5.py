from __future__ import annotations

import unittest

from domain import Candle
from scenario_scale_ablation_v5 import (
    MacroOnlyResearchScenarioBundleV5,
    MicroOnlyResearchScenarioBundleV5,
)

NS = 60_000_000_000


def candle(index: int) -> Candle:
    return Candle(index * NS, 100.0, 101.0, 99.0, 100.5, 1.0)


class ScaleAblationBundleTests(unittest.TestCase):
    def test_macro_only_preserves_audit_bars_without_running_micro(self) -> None:
        bundle = MacroOnlyResearchScenarioBundleV5("TEST", 0.1)
        bundle.on_bar(1, candle(1))
        bundle.on_bar(60, candle(2))
        self.assertEqual(len(bundle.detectors[1].bars), 1)
        self.assertEqual(len(bundle.detectors[60].bars), 1)
        self.assertEqual(len(bundle.macro.structure.bars), 1)
        self.assertEqual(len(bundle.micro.structure.bars), 0)
        self.assertEqual(len(bundle.micro.trigger_detector.bars), 0)
        self.assertTrue(bundle.diagnostics["scale_ablation"]["macro_enabled"])
        self.assertFalse(bundle.diagnostics["scale_ablation"]["micro_enabled"])

    def test_micro_only_does_not_advance_macro_engine(self) -> None:
        bundle = MicroOnlyResearchScenarioBundleV5("TEST", 0.1)
        bundle.on_bar(60, candle(1))
        bundle.on_bar(15, candle(2))
        bundle.on_bar(1, candle(3))
        self.assertEqual(len(bundle.detectors[60].bars), 1)
        self.assertEqual(len(bundle.macro.structure.bars), 0)
        self.assertEqual(len(bundle.micro.structure.bars), 1)
        self.assertEqual(len(bundle.micro.trigger_detector.bars), 1)
        self.assertFalse(bundle.diagnostics["scale_ablation"]["macro_enabled"])
        self.assertTrue(bundle.diagnostics["scale_ablation"]["micro_enabled"])


if __name__ == "__main__":
    unittest.main()
