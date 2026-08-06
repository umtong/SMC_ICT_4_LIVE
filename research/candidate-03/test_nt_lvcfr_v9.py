from __future__ import annotations

import unittest
from pathlib import Path

from nt_lvcfr_data import CandidateConfig


class CompletedStructureProtectionTests(unittest.TestCase):
    def test_live_strategy_arms_then_trails_completed_structure(self) -> None:
        source = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("STRUCTURAL_TRAIL_ARMED", source)
        self.assertIn("def _structural_protection_stop", source)
        self.assertIn("COMPLETED_TWENTY_MINUTE_STRUCTURE_ADVANCED", source)
        self.assertIn("structural_trail_updates", source)
        self.assertIn(
            "active.direction * (executable - structural_stop) > 0.0",
            source,
        )
        self.assertNotIn(
            "active.stop = (\n                max(active.stop, structural_trigger)",
            source,
        )

    def test_v9_changes_only_the_protection_mechanism(self) -> None:
        v8 = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v8_config.json"))
        v9 = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v9_config.json"))
        self.assertEqual(v9.first_displacement_bp, v8.first_displacement_bp)
        self.assertEqual(v9.risk_fraction, 0.03)
        self.assertEqual(v9.continuation_trail_minutes, 20)
        self.assertEqual(v9.continuation_trail_buffer_atr, 0.05)
        self.assertEqual(v9.continuation_target_net_r, v8.continuation_target_net_r)
        self.assertEqual(v9.reversal_target_net_r, v8.reversal_target_net_r)
        self.assertEqual(v9.validation_weeks, v8.validation_weeks)

    def test_native_strategy_remains_the_only_execution_path(self) -> None:
        source = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        self.assertIn("self.submit_order(order)", source)
        self.assertIn("self.portfolio.net_position", source)
        self.assertNotIn("synthetic_nav", source)
        self.assertNotIn("simulate_fill", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
