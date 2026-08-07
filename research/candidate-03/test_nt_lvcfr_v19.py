from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v19_signals import (
    TradeBlock,
    block_features,
    cumulative_features,
)
from nt_lvcfr_data import CandidateConfig


class V19ExecutedFlowTests(unittest.TestCase):
    def test_buyer_and_seller_aggressor_signs_are_opposite(self) -> None:
        block = TradeBlock()
        block.add(100.0, 2.0, False)  # buyer aggressor
        block.add(101.0, 1.0, True)   # seller aggressor
        self.assertAlmostEqual(block.gross_notional, 301.0)
        self.assertAlmostEqual(block.signed_notional, 99.0)
        features = block_features(
            block,
            direction=1,
            baseline_median_gross=301.0,
        )
        self.assertIsNotNone(features)
        assert features is not None
        self.assertGreater(features.directional_flow, 0.0)
        self.assertGreater(features.progress_bp, 0.0)

    def test_response_is_price_progress_per_relative_activity(self) -> None:
        block = TradeBlock()
        block.add(100.0, 1.0, False)
        block.add(101.0, 1.0, False)
        features = block_features(
            block,
            direction=1,
            baseline_median_gross=block.gross_notional / 2.0,
        )
        self.assertIsNotNone(features)
        assert features is not None
        self.assertAlmostEqual(features.activity_ratio, 2.0)
        self.assertAlmostEqual(
            features.response_score,
            features.progress_bp / 2.0,
        )

    def test_cumulative_features_preserve_event_order(self) -> None:
        first = TradeBlock()
        first.add(100.0, 1.0, False)
        first.add(101.0, 1.0, False)
        second = TradeBlock()
        second.add(101.0, 1.0, False)
        second.add(102.0, 1.0, False)
        features = cumulative_features(
            [first, second],
            direction=1,
            baseline_median_gross=(first.gross_notional + second.gross_notional) / 2.0,
        )
        self.assertIsNotNone(features)
        assert features is not None
        self.assertGreater(features.progress_bp, 0.0)
        self.assertGreater(features.directional_flow, 0.0)
        self.assertGreater(features.path_efficiency, 0.0)
        self.assertLessEqual(features.path_efficiency, 1.01)

    def test_project_risk_and_native_execution_contract_are_fixed(self) -> None:
        root = Path(__file__).resolve().parent
        config = CandidateConfig.load(root / "nt_lvcfr_v19_config.json")
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(
            config.validation_weeks,
            ("2024-01-08", "2025-06-23", "2022-05-16"),
        )
        strategy = (root / "nt_lvcfr_strategy.py").read_text(encoding="utf-8")
        runner = (root / "run_nt_lvcfr.py").read_text(encoding="utf-8")
        preparation = (root / "prepare_nt_lvcfr_v19.py").read_text(encoding="utf-8")
        self.assertIn("self.submit_order(order)", strategy)
        self.assertIn("BacktestNode", runner)
        self.assertIn("futures/um/daily/aggTrades", preparation)
        self.assertIn("spot/daily/aggTrades", preparation)
        self.assertNotIn("simulate_fill", strategy)
        self.assertNotIn("synthetic_nav", strategy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
