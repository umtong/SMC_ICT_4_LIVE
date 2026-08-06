from __future__ import annotations

import unittest
from pathlib import Path

from nt_lvcfr_data import CandidateConfig


class ScenarioAwareProtectionTests(unittest.TestCase):
    def test_live_strategy_routes_protection_by_causal_state(self) -> None:
        source = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'boundary_invalidation = scenario_kind == "VALUE_EDGE_CONTINUATION"',
            source,
        )
        self.assertIn("VALUE_EDGE_BOUNDARY_PROTECTION_ACTIVATED", source)
        self.assertIn("PRIOR_RANGE_EXTERNAL_BECAME_CAUSAL_INVALIDATION", source)
        self.assertIn("INTERMEDIATE_LIQUIDITY_WAYPOINT_TRAIL_ARMED", source)
        self.assertIn("FIRST_OBJECTIVE_IS_WAYPOINT_NOT_EXACT_INVALIDATION", source)
        self.assertIn("def _structural_protection_stop", source)
        self.assertIn("waypoint_structure_trail_activations", source)

    def test_v11_changes_only_state_to_protection_mapping(self) -> None:
        v10 = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v10_config.json"))
        v11 = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v11_config.json"))
        self.assertEqual(v11.first_displacement_bp, v10.first_displacement_bp)
        self.assertEqual(v11.second_activity_min, v10.second_activity_min)
        self.assertEqual(v11.second_futures_flow_max, v10.second_futures_flow_max)
        self.assertEqual(v11.second_spot_flow_min, v10.second_spot_flow_min)
        self.assertEqual(v11.total_oi_drop_bp, v10.total_oi_drop_bp)
        self.assertEqual(v11.initial_stop_buffer_atr, v10.initial_stop_buffer_atr)
        self.assertEqual(v11.continuation_trail_minutes, 20)
        self.assertEqual(v11.continuation_trail_buffer_atr, 0.05)
        self.assertEqual(v11.continuation_target_net_r, v10.continuation_target_net_r)
        self.assertEqual(v11.reversal_target_net_r, v10.reversal_target_net_r)
        self.assertEqual(v11.risk_fraction, 0.03)
        self.assertEqual(v11.validation_weeks, v10.validation_weeks)

    def test_native_strategy_remains_the_only_execution_path(self) -> None:
        source = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        self.assertIn("self.submit_order(order)", source)
        self.assertIn("self.portfolio.net_position", source)
        self.assertNotIn("synthetic_nav", source)
        self.assertNotIn("simulate_fill", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
