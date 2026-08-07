from __future__ import annotations

import unittest
from pathlib import Path

from derive_nt_lvcfr_v19_signals import (
    BASELINE_BLOCKS,
    BLOCK_NS,
    CandidateContext,
    TradeBlock,
    block_features,
    cumulative_features,
    rolling_horizon_thresholds,
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
        second.add(103.0, 1.0, False)
        second.add(104.0, 1.0, False)
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


    def test_candidate_context_excludes_inventory_event_gap(self) -> None:
        baseline_start = 0
        baseline_end = BASELINE_BLOCKS * BLOCK_NS
        observation_start = baseline_end + 10 * 60 * 1_000_000_000
        observation_end = observation_start + 6 * BLOCK_NS
        context = CandidateContext(
            candidate={"scenario_id": "TEST"},
            baseline_start_ns=baseline_start,
            baseline_end_ns=baseline_end,
            observation_start_ns=observation_start,
            observation_end_ns=observation_end,
        )
        context.add(BLOCK_NS, 100.0, 1.0, False)
        context.add(baseline_end + BLOCK_NS, 999.0, 1.0, False)
        context.add(observation_start + BLOCK_NS, 101.0, 1.0, False)
        self.assertEqual(sum(block.trades for block in context.blocks), 2)
        self.assertEqual(context.blocks[1].trades, 1)
        self.assertEqual(
            context.blocks[BASELINE_BLOCKS + 1].trades,
            1,
        )
        retained_prices = {
            price
            for block in context.blocks
            for price in (block.first_price, block.last_price)
            if price is not None
        }
        self.assertNotIn(999.0, retained_prices)

    def test_horizon_thresholds_use_equal_length_pre_event_windows(self) -> None:
        blocks = []
        price = 100.0
        for index in range(BASELINE_BLOCKS):
            block = TradeBlock()
            block.add(price, 1.0, False)
            price *= 1.0001 if index % 2 == 0 else 0.99995
            block.add(price, 1.0, index % 3 == 0)
            blocks.append(block)
        median_gross = sorted(
            block.gross_notional for block in blocks
        )[len(blocks) // 2]
        thresholds = rolling_horizon_thresholds(
            blocks,
            direction=1,
            baseline_median_gross=median_gross,
        )
        self.assertEqual(set(thresholds), set(range(2, 7)))
        self.assertGreaterEqual(
            thresholds[6]["baseline_windows"],
            10.0,
        )
        source = Path(__file__).with_name(
            "derive_nt_lvcfr_v19_signals.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "current_thresholds = horizon_thresholds.get(count)",
            source,
        )
        self.assertNotIn("futures_baseline_features = [", source)
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
        self.assertIn("args.week_start - timedelta(days=1)", preparation)
        self.assertNotIn("simulate_fill", strategy)
        self.assertNotIn("synthetic_nav", strategy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
