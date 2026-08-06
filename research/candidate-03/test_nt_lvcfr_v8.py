from __future__ import annotations

import unittest
from pathlib import Path

from nt_lvcfr_data import CandidateConfig


class StructuralRatchetContractTests(unittest.TestCase):
    def test_patch_anchors_first_stop_at_structural_level(self) -> None:
        source = Path(__file__).with_name("apply_nt_lvcfr_v8_strategy_patch.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("max(active.stop, structural_trigger)", source)
        self.assertIn("min(active.stop, structural_trigger)", source)
        self.assertIn("AFTER_COST_BREAK_EVEN_TRADED_AFTER_FIRST_OBJECTIVE", source)
        self.assertNotIn("active.stop = (\n                max(active.stop, active.break_even_price)", source)

    def test_v8_changes_no_detector_or_risk_configuration(self) -> None:
        config = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v8_config.json"))
        self.assertEqual(config.first_displacement_bp, 12.0)
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(config.continuation_target_net_r, 3.0)
        self.assertEqual(config.reversal_target_net_r, 1.5)
        self.assertEqual(
            config.validation_weeks,
            ("2024-01-08", "2025-06-23", "2022-05-16"),
        )

    def test_native_strategy_remains_the_only_execution_path(self) -> None:
        source = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        self.assertIn("self.submit_order(order)", source)
        self.assertIn("self.portfolio.net_position", source)
        self.assertNotIn("synthetic_nav", source)
        self.assertNotIn("simulate_fill", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
