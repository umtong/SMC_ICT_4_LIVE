from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v13_signals import (
    MEASURED_ACCEPTANCE_CONTINUATION,
    MIDPOINT_FAILURE_CHOCH_REVERSAL,
    first_completed_range_break,
    resolve_same_side_pending_auction,
)
from nt_lvcfr_data import CandidateConfig


class SequentialAuctionTests(unittest.TestCase):
    def test_wick_beyond_extreme_is_not_a_completed_break(self) -> None:
        futures = {
            10: {"open": 100.0, "high": 112.0, "low": 99.0, "close": 109.0},
            11: {"open": 109.0, "high": 112.0, "low": 108.0, "close": 111.0},
        }
        result = first_completed_range_break(
            futures,
            start_minute=10,
            event_low=90.0,
            event_high=110.0,
            expiry_minutes=2,
        )
        self.assertEqual(result, (11, 1, 111.0))

    def test_measured_extension_confirms_same_side_acceptance(self) -> None:
        futures = {
            20: {"open": 111.0, "high": 118.0, "low": 110.0, "close": 117.0},
            21: {"open": 117.0, "high": 131.0, "low": 116.0, "close": 131.0},
        }
        result = resolve_same_side_pending_auction(
            futures,
            start_minute=20,
            first_break_direction=1,
            event_low=90.0,
            event_high=110.0,
            expiry_minutes=2,
        )
        self.assertEqual(
            result,
            (21, MEASURED_ACCEPTANCE_CONTINUATION, 1, 131.0),
        )

    def test_midpoint_failure_confirms_choch_reversal(self) -> None:
        futures = {
            30: {"open": 112.0, "high": 113.0, "low": 99.0, "close": 99.0},
            31: {"open": 99.0, "high": 100.0, "low": 89.0, "close": 89.0},
        }
        result = resolve_same_side_pending_auction(
            futures,
            start_minute=30,
            first_break_direction=1,
            event_low=90.0,
            event_high=110.0,
            expiry_minutes=2,
        )
        self.assertEqual(
            result,
            (30, MIDPOINT_FAILURE_CHOCH_REVERSAL, -1, 99.0),
        )

    def test_first_completed_structural_outcome_wins(self) -> None:
        futures = {
            40: {"open": 89.0, "high": 101.0, "low": 88.0, "close": 101.0},
            41: {"open": 101.0, "high": 132.0, "low": 100.0, "close": 131.0},
        }
        result = resolve_same_side_pending_auction(
            futures,
            start_minute=40,
            first_break_direction=-1,
            event_low=90.0,
            event_high=110.0,
            expiry_minutes=2,
        )
        self.assertEqual(
            result,
            (40, MIDPOINT_FAILURE_CHOCH_REVERSAL, 1, 101.0),
        )

    def test_missing_minute_invalidates_pending_state(self) -> None:
        futures = {
            50: {"open": 111.0, "high": 115.0, "low": 108.0, "close": 114.0},
            52: {"open": 114.0, "high": 132.0, "low": 113.0, "close": 131.0},
        }
        self.assertIsNone(
            resolve_same_side_pending_auction(
                futures,
                start_minute=50,
                first_break_direction=1,
                event_low=90.0,
                event_high=110.0,
                expiry_minutes=3,
            )
        )


class V13ContractTests(unittest.TestCase):
    def test_v13_keeps_detector_execution_risk_and_validation_order(self) -> None:
        v12 = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v12_config.json"))
        v13 = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v13_config.json"))
        self.assertEqual(v13.first_displacement_bp, v12.first_displacement_bp)
        self.assertEqual(v13.second_activity_min, v12.second_activity_min)
        self.assertEqual(v13.second_futures_flow_max, v12.second_futures_flow_max)
        self.assertEqual(v13.second_spot_flow_min, v12.second_spot_flow_min)
        self.assertEqual(v13.total_oi_drop_bp, v12.total_oi_drop_bp)
        self.assertEqual(v13.initial_stop_buffer_atr, 0.20)
        self.assertEqual(v13.continuation_target_net_r, 3.0)
        self.assertEqual(v13.reversal_target_net_r, 1.5)
        self.assertEqual(v13.risk_fraction, 0.03)
        self.assertEqual(v13.validation_weeks, v12.validation_weeks)

    def test_router_contains_no_fill_or_nav_simulation(self) -> None:
        source = Path(__file__).with_name("derive_nt_lvcfr_v13_signals.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("MEASURED_ACCEPTANCE_VERSUS_MIDPOINT_FAILURE", source)
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("synthetic_nav", source)
        strategy = Path(__file__).with_name("nt_lvcfr_strategy.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("self.submit_order(order)", strategy)
        self.assertIn("self.portfolio.net_position", strategy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
