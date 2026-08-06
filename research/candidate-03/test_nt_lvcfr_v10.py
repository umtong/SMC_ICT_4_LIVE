from __future__ import annotations

import unittest
from pathlib import Path

from nt_lvcfr_data import CandidateConfig


class StructuralObjectiveBufferTests(unittest.TestCase):
    def test_live_strategy_places_stop_behind_causal_objective(self) -> None:
        source = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("STRUCTURAL_OBJECTIVE_BUFFER_ACTIVATED", source)
        self.assertIn(
            "buffered_stop = structural_trigger - active.direction * buffer",
            source,
        )
        self.assertIn(
            "FIRST_CAUSAL_LIQUIDITY_OBJECTIVE_HELD_WITH_FROZEN_ATR_BUFFER",
            source,
        )
        self.assertIn("structural_objective_buffer_activations", source)
        self.assertNotIn("def _structural_protection_stop", source)
        self.assertNotIn("COMPLETED_TWENTY_MINUTE_STRUCTURE_ADVANCED", source)

    def test_v10_changes_no_detector_entry_target_or_risk_configuration(self) -> None:
        v9 = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v9_config.json"))
        v10 = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v10_config.json"))
        self.assertEqual(v10.first_displacement_bp, v9.first_displacement_bp)
        self.assertEqual(v10.second_activity_min, v9.second_activity_min)
        self.assertEqual(v10.total_oi_drop_bp, v9.total_oi_drop_bp)
        self.assertEqual(v10.initial_stop_buffer_atr, v9.initial_stop_buffer_atr)
        self.assertEqual(v10.continuation_trail_buffer_atr, 0.05)
        self.assertEqual(v10.continuation_target_net_r, v9.continuation_target_net_r)
        self.assertEqual(v10.reversal_target_net_r, v9.reversal_target_net_r)
        self.assertEqual(v10.risk_fraction, 0.03)
        self.assertEqual(v10.validation_weeks, v9.validation_weeks)

    def test_native_strategy_remains_the_only_execution_path(self) -> None:
        source = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        self.assertIn("self.submit_order(order)", source)
        self.assertIn("self.portfolio.net_position", source)
        self.assertNotIn("synthetic_nav", source)
        self.assertNotIn("simulate_fill", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
