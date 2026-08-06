from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v7_signals import find_external_reacceptance
from nt_lvcfr_data import CandidateConfig


class FailedReclaimSequenceTests(unittest.TestCase):
    def test_reacceptance_requires_completed_close_beyond_external(self) -> None:
        futures = {
            10: {"open": 99.0, "high": 101.0, "low": 98.0, "close": 99.5},
            11: {"open": 99.5, "high": 102.0, "low": 99.0, "close": 100.5},
        }
        result = find_external_reacceptance(
            futures,
            start_minute=10,
            original_direction=1,
            directional_external=100.0,
            expiry_minutes=2,
        )
        self.assertIsNotNone(result)
        minute, observed = result
        self.assertEqual(minute, 11)
        self.assertEqual(len(observed), 2)

    def test_intrabar_touch_without_close_does_not_reaccept(self) -> None:
        futures = {
            10: {"open": 99.0, "high": 102.0, "low": 98.0, "close": 99.5},
        }
        self.assertIsNone(
            find_external_reacceptance(
                futures,
                start_minute=10,
                original_direction=1,
                directional_external=100.0,
                expiry_minutes=1,
            )
        )

    def test_short_reacceptance_is_symmetric(self) -> None:
        futures = {
            10: {"open": 101.0, "high": 102.0, "low": 99.0, "close": 99.5},
        }
        result = find_external_reacceptance(
            futures,
            start_minute=10,
            original_direction=-1,
            directional_external=100.0,
            expiry_minutes=1,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 10)

    def test_missing_minute_expires_state_instead_of_skipping_time(self) -> None:
        futures = {
            10: {"open": 99.0, "high": 100.0, "low": 98.0, "close": 99.5},
            12: {"open": 99.5, "high": 102.0, "low": 99.0, "close": 101.0},
        }
        self.assertIsNone(
            find_external_reacceptance(
                futures,
                start_minute=10,
                original_direction=1,
                directional_external=100.0,
                expiry_minutes=3,
            )
        )


class V7ContractTests(unittest.TestCase):
    def test_v7_reuses_frozen_12bp_impulse_definition(self) -> None:
        source = Path(__file__).with_name("derive_nt_lvcfr_v7_signals.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("minimum_reclaim_displacement_bp: float = 12.0", source)
        self.assertIn("FAILED_RECLAIM_REACCEPTANCE_CONTINUATION", source)
        self.assertIn("disable_rapid_failure_reversal", source)

    def test_v7_keeps_project_risk_and_validation_order(self) -> None:
        config = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v7_config.json"))
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(config.first_displacement_bp, 12.0)
        self.assertEqual(
            config.validation_weeks,
            ("2024-01-08", "2025-06-23", "2022-05-16"),
        )

    def test_router_contains_no_fill_or_nav_simulation(self) -> None:
        source = Path(__file__).with_name("derive_nt_lvcfr_v7_signals.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("realized_pnl =", source)
        self.assertNotIn("nav *=", source)
        self.assertNotIn("commission =", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
