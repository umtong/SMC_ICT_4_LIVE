from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v20_signals import FairValueGap
from derive_nt_lvcfr_v21_signals import (
    find_failed_gap_retest_rejection,
    find_fvg_failure,
)
from nt_lvcfr_data import CandidateConfig


def bar(
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> dict[str, float]:
    return {
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    }


class V21FailedFvgAuctionRetestTests(unittest.TestCase):
    def test_undefended_far_edge_close_confirms_failure(self) -> None:
        gap = FairValueGap(direction=1, formed_minute=2, lower=100.0, upper=102.0)
        futures = {3: bar(103.0, 103.2, 99.0, 99.5)}
        failure, reason = find_fvg_failure(
            futures,
            gap=gap,
            start_minute=3,
            expiry_minutes=1,
        )
        self.assertIsNotNone(failure)
        assert failure is not None
        self.assertEqual(reason, "UNDEFENDED_FVG_FAILURE")
        self.assertEqual(failure.failure_mode, "UNDEFENDED_FVG_FAILURE")
        self.assertIsNone(failure.defended_minute)

    def test_apparent_defense_then_far_edge_close_is_distinct(self) -> None:
        gap = FairValueGap(direction=1, formed_minute=2, lower=100.0, upper=102.0)
        futures = {
            3: bar(100.8, 102.8, 100.5, 102.4),
            4: bar(102.4, 102.5, 99.2, 99.7),
        }
        failure, reason = find_fvg_failure(
            futures,
            gap=gap,
            start_minute=3,
            expiry_minutes=2,
        )
        self.assertIsNotNone(failure)
        assert failure is not None
        self.assertEqual(reason, "DEFENDED_FVG_FAILURE")
        self.assertEqual(failure.defended_minute, 3)
        self.assertEqual(failure.touches_before_failure, 1)

    def test_failed_bullish_gap_requires_retest_and_bearish_rejection(self) -> None:
        gap = FairValueGap(direction=1, formed_minute=2, lower=100.0, upper=102.0)
        futures = {
            5: bar(99.3, 99.8, 98.8, 99.6),
            6: bar(100.5, 101.0, 99.1, 99.4),
        }
        minute, row, touches = find_failed_gap_retest_rejection(
            futures,
            gap=gap,
            start_minute=5,
            expiry_minutes=2,
        )
        self.assertEqual(minute, 6)
        self.assertEqual(touches, 1)
        assert row is not None
        self.assertLess(row["close"], gap.lower)
        self.assertLess(row["close"], row["open"])

    def test_failed_bearish_gap_requires_retest_and_bullish_rejection(self) -> None:
        gap = FairValueGap(direction=-1, formed_minute=2, lower=98.0, upper=100.0)
        futures = {
            5: bar(100.7, 101.2, 100.4, 100.8),
            6: bar(99.6, 101.0, 99.2, 100.6),
        }
        minute, row, touches = find_failed_gap_retest_rejection(
            futures,
            gap=gap,
            start_minute=5,
            expiry_minutes=2,
        )
        self.assertEqual(minute, 6)
        self.assertEqual(touches, 1)
        assert row is not None
        self.assertGreater(row["close"], gap.upper)
        self.assertGreater(row["close"], row["open"])

    def test_full_gap_reclaim_invalidates_reversal_before_entry(self) -> None:
        gap = FairValueGap(direction=1, formed_minute=2, lower=100.0, upper=102.0)
        futures = {5: bar(99.5, 102.5, 99.0, 102.1)}
        minute, row, reason = find_failed_gap_retest_rejection(
            futures,
            gap=gap,
            start_minute=5,
            expiry_minutes=1,
        )
        self.assertIsNone(minute)
        self.assertIsNone(row)
        self.assertEqual(reason, "FAILED_GAP_FULLY_RECLAIMED")

    def test_native_execution_and_fixed_risk_contract_remain_unchanged(self) -> None:
        root = Path(__file__).resolve().parent
        config = CandidateConfig.load(root / "nt_lvcfr_v19_config.json")
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(
            config.validation_weeks,
            ("2024-01-08", "2025-06-23", "2022-05-16"),
        )
        source = (root / "derive_nt_lvcfr_v21_signals.py").read_text(
            encoding="utf-8"
        )
        strategy = (root / "nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        runner = (root / "run_nt_lvcfr.py").read_text(encoding="utf-8")
        self.assertIn(
            "confirm_ns = (rejection_minute + 1) * NS_PER_MINUTE",
            source,
        )
        self.assertIn("self.submit_order(order)", strategy)
        self.assertIn("BacktestNode", runner)
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("synthetic_nav", source)
        self.assertNotIn("episode_pnl", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
