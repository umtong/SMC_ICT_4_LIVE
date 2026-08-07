from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v20_signals import (
    FairValueGap,
    directional_fvg,
    find_active_displacement_fvg,
    find_retrace_defense,
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


class V20FvgRetraceDefenseTests(unittest.TestCase):
    def test_directional_fvg_requires_gap_and_directional_body(self) -> None:
        bullish = directional_fvg(
            bar(99.0, 100.0, 98.0, 99.5),
            bar(99.5, 103.0, 99.4, 102.5),
            bar(102.2, 104.0, 102.0, 103.0),
            direction=1,
            formed_minute=2,
        )
        self.assertEqual(
            bullish,
            FairValueGap(direction=1, formed_minute=2, lower=100.0, upper=102.0),
        )
        no_gap = directional_fvg(
            bar(99.0, 102.5, 98.0, 99.5),
            bar(99.5, 103.0, 99.4, 102.5),
            bar(102.2, 104.0, 102.0, 103.0),
            direction=1,
            formed_minute=2,
        )
        self.assertIsNone(no_gap)

    def test_latest_unfilled_gap_from_structural_leg_is_selected(self) -> None:
        futures = {
            0: bar(99.0, 100.0, 98.0, 99.5),
            1: bar(99.5, 103.0, 99.4, 102.5),
            2: bar(102.2, 104.0, 102.0, 103.0),
            3: bar(103.0, 105.0, 102.6, 104.0),
            4: bar(104.0, 106.0, 103.5, 105.0),
        }
        gap, mode = find_active_displacement_fvg(
            futures,
            direction=1,
            structural_start_minute=0,
            known_minute=4,
            formation_expiry_minutes=3,
        )
        self.assertIsNotNone(gap)
        assert gap is not None
        self.assertEqual(gap.formed_minute, 2)
        self.assertEqual(mode, "ACTIVE_FVG_FROM_STRUCTURAL_LEG")

    def test_completed_touch_and_midpoint_defense_is_causal(self) -> None:
        gap = FairValueGap(direction=1, formed_minute=2, lower=100.0, upper=102.0)
        futures = {
            3: bar(103.0, 104.0, 102.4, 103.2),
            4: bar(103.2, 103.5, 101.2, 101.4),
            5: bar(101.4, 103.0, 100.8, 102.6),
        }
        minute, row, touches = find_retrace_defense(
            futures,
            gap=gap,
            start_minute=3,
            expiry_minutes=3,
        )
        self.assertEqual(minute, 5)
        self.assertEqual(touches, 2)
        assert row is not None
        self.assertGreater(row["close"], gap.midpoint)

    def test_completed_close_through_far_edge_invalidates(self) -> None:
        gap = FairValueGap(direction=-1, formed_minute=2, lower=98.0, upper=100.0)
        futures = {
            3: bar(97.0, 99.0, 96.0, 98.5),
            4: bar(98.5, 101.0, 98.0, 100.2),
        }
        minute, row, reason = find_retrace_defense(
            futures,
            gap=gap,
            start_minute=3,
            expiry_minutes=2,
        )
        self.assertIsNone(minute)
        self.assertIsNone(row)
        self.assertEqual(reason, "FVG_FAR_EDGE_CLOSE_INVALIDATION")

    def test_native_execution_and_fixed_risk_contract_remain_unchanged(self) -> None:
        root = Path(__file__).resolve().parent
        config = CandidateConfig.load(root / "nt_lvcfr_v19_config.json")
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(
            config.validation_weeks,
            ("2024-01-08", "2025-06-23", "2022-05-16"),
        )
        source = (root / "derive_nt_lvcfr_v20_signals.py").read_text(
            encoding="utf-8"
        )
        strategy = (root / "nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        runner = (root / "run_nt_lvcfr.py").read_text(encoding="utf-8")
        self.assertIn(
            'confirm_ns = (defense_minute + 1) * NS_PER_MINUTE',
            source,
        )
        self.assertIn("self.submit_order(order)", strategy)
        self.assertIn("BacktestNode", runner)
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("synthetic_nav", source)
        self.assertNotIn("episode_pnl", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
