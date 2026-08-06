from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v5_signals import (
    find_migration_reclaim,
    opposite_range_migration_fraction,
)
from nt_lvcfr_data import CandidateConfig


class RangeMigrationTests(unittest.TestCase):
    def test_opposite_migration_is_normalized_by_dealing_range(self) -> None:
        prior = [{"open": 100.0, "close": 100.0}, {"open": 100.0, "close": 180.0}]
        self.assertAlmostEqual(
            opposite_range_migration_fraction(
                prior,
                event_direction=-1,
                dealing_range_span=100.0,
            ),
            0.8,
        )

    def test_same_direction_migration_is_not_reversal_evidence(self) -> None:
        prior = [{"open": 100.0, "close": 100.0}, {"open": 100.0, "close": 180.0}]
        self.assertEqual(
            opposite_range_migration_fraction(
                prior,
                event_direction=1,
                dealing_range_span=100.0,
            ),
            0.0,
        )


class ReclaimTests(unittest.TestCase):
    def test_reclaim_waits_for_completed_close_through_midpoint(self) -> None:
        futures = {
            10: {"open": 95.0, "high": 101.0, "low": 94.0, "close": 99.0},
            11: {"open": 99.0, "high": 102.0, "low": 98.0, "close": 101.0},
        }
        result = find_migration_reclaim(
            futures,
            event_end_minute=10,
            event_midpoint=100.0,
            migration_direction=1,
            expiry_minutes=2,
        )
        self.assertIsNotNone(result)
        reclaim_minutes, observed = result
        self.assertEqual(reclaim_minutes, 2)
        self.assertEqual(len(observed), 2)

    def test_intrabar_touch_without_close_is_not_reclaim(self) -> None:
        futures = {
            10: {"open": 95.0, "high": 105.0, "low": 94.0, "close": 99.0},
        }
        self.assertIsNone(
            find_migration_reclaim(
                futures,
                event_end_minute=10,
                event_midpoint=100.0,
                migration_direction=1,
                expiry_minutes=1,
            )
        )

    def test_missing_minute_invalidates_reclaim_sequence(self) -> None:
        futures = {10: {"open": 95.0, "high": 99.0, "low": 94.0, "close": 98.0}}
        self.assertIsNone(
            find_migration_reclaim(
                futures,
                event_end_minute=10,
                event_midpoint=100.0,
                migration_direction=1,
                expiry_minutes=2,
            )
        )


class ConfigAndEngineContractTests(unittest.TestCase):
    def test_v5_keeps_project_risk_fraction_and_validation_order(self) -> None:
        config = CandidateConfig.load(Path(__file__).with_name("nt_lvcfr_v5_config.json"))
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(
            config.validation_weeks,
            ("2024-01-08", "2025-06-23", "2022-05-16"),
        )

    def test_catalog_rebuild_delegates_to_nautilus_catalog_builder(self) -> None:
        source = Path(__file__).with_name(
            "rebuild_nt_lvcfr_trade_proxy_catalog.py"
        ).read_text(encoding="utf-8")
        self.assertIn("build_trade_proxy_catalog", source)
        self.assertNotIn("realized_pnl =", source)
        self.assertNotIn("nav *=", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
