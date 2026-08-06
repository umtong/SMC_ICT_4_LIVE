from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v12_signals import first_completed_event_range_break
from nt_lvcfr_data import CandidateConfig


class EventRangeResolutionTests(unittest.TestCase):
    def test_intrabar_touch_is_not_a_completed_bos(self) -> None:
        futures = {
            10: {"open": 100.0, "high": 111.0, "low": 99.0, "close": 109.0},
            11: {"open": 109.0, "high": 112.0, "low": 108.0, "close": 111.0},
        }
        result = first_completed_event_range_break(
            futures,
            start_minute=10,
            event_low=90.0,
            event_high=110.0,
            expiry_minutes=2,
        )
        self.assertIsNotNone(result)
        assert result is not None
        minute, direction, close, observed = result
        self.assertEqual(minute, 11)
        self.assertEqual(direction, 1)
        self.assertEqual(close, 111.0)
        self.assertEqual(len(observed), 2)

    def test_first_completed_side_wins_symmetrically(self) -> None:
        futures = {
            20: {"open": 100.0, "high": 101.0, "low": 89.0, "close": 89.5},
            21: {"open": 89.5, "high": 112.0, "low": 89.0, "close": 111.0},
        }
        result = first_completed_event_range_break(
            futures,
            start_minute=20,
            event_low=90.0,
            event_high=110.0,
            expiry_minutes=2,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[0], 20)
        self.assertEqual(result[1], -1)
        self.assertEqual(result[2], 89.5)

    def test_missing_or_unresolved_sequence_emits_no_break(self) -> None:
        unresolved = {
            30: {"open": 100.0, "high": 109.0, "low": 91.0, "close": 101.0},
            31: {"open": 101.0, "high": 108.0, "low": 92.0, "close": 99.0},
        }
        self.assertIsNone(
            first_completed_event_range_break(
                unresolved,
                start_minute=30,
                event_low=90.0,
                event_high=110.0,
                expiry_minutes=2,
            )
        )
        self.assertIsNone(
            first_completed_event_range_break(
                {40: unresolved[30]},
                start_minute=40,
                event_low=90.0,
                event_high=110.0,
                expiry_minutes=2,
            )
        )


class V12ContractTests(unittest.TestCase):
    def test_v12_keeps_detector_risk_and_validation_order(self) -> None:
        v11 = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v11_config.json"))
        v12 = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v12_config.json"))
        self.assertEqual(v12.first_displacement_bp, v11.first_displacement_bp)
        self.assertEqual(v12.second_activity_min, v11.second_activity_min)
        self.assertEqual(v12.second_futures_flow_max, v11.second_futures_flow_max)
        self.assertEqual(v12.second_spot_flow_min, v11.second_spot_flow_min)
        self.assertEqual(v12.total_oi_drop_bp, v11.total_oi_drop_bp)
        self.assertEqual(v12.risk_fraction, 0.03)
        self.assertEqual(v12.validation_weeks, v11.validation_weeks)

    def test_router_contains_no_execution_or_nav_simulator(self) -> None:
        source = Path(__file__).with_name("derive_nt_lvcfr_v12_signals.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("first_completed_event_range_break", source)
        self.assertIn("MIDPOINT_INVALIDATION", source)
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("synthetic_nav", source)
        strategy = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("self.submit_order(order)", strategy)
        self.assertIn("self.portfolio.net_position", strategy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
