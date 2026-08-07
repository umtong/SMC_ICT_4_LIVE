from __future__ import annotations

from pathlib import Path
import unittest

from market_leadership import LeadershipDecision
from runner_materializer_v4 import materialize_runner_source
from semantic_market_leadership import AAC_ALIGNED, AAC_LAGGARD_TRANSFER
from semantic_market_leadership_v4 import AAC_EARLY_REPRICING, refine_v3_decision


ROOT = Path(__file__).resolve().parent
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class V4AuctionSemanticsTests(unittest.TestCase):
    def decision(self, **updates):
        values = dict(
            approved=True,
            reason=AAC_ALIGNED,
            leader="BTCUSDT",
            symbol="SOLUSDT",
            scenario="AAC",
            direction="LONG",
            sweep_ts_ns=1,
            confirmation_ts_ns=2,
            peer_returns={"BTCUSDT": 0.003, "ETHUSDT": 0.002, "XRPUSDT": 0.001},
            directional_returns={symbol: 0.01 for symbol in SYMBOLS},
            directional_trend_scores={
                "BTCUSDT": 0.31,
                "ETHUSDT": 0.29,
                "SOLUSDT": 0.23,
                "XRPUSDT": 0.33,
            },
            candidate_event_move=0.01,
            peer_event_median=0.002,
            confirmation_impulse=1.8,
            trailing_direction_rank=3,
            event_direction_rank=1,
            event_path_efficiency=0.20,
            event_standardized_displacement=1.20,
        )
        values.update(updates)
        return LeadershipDecision(**values)

    def refine(self, decision):
        return refine_v3_decision(
            decision,
            symbol_count=4,
            completed_auction_unit=0.50,
        )

    def test_early_ordinary_aac_is_accepted(self):
        result = self.refine(self.decision())
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, AAC_EARLY_REPRICING)

    def test_extended_candidate_is_not_called_acceptance(self):
        scores = {"BTCUSDT": 0.29, "ETHUSDT": 0.31, "SOLUSDT": 1.05, "XRPUSDT": 0.24}
        result = self.refine(self.decision(directional_trend_scores=scores))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_COMPLETED_AUCTION_ALREADY_EXTENDED")

    def test_extended_market_is_not_called_acceptance(self):
        scores = {"BTCUSDT": 0.72, "ETHUSDT": 0.38, "SOLUSDT": 0.39, "XRPUSDT": 0.69}
        result = self.refine(self.decision(directional_trend_scores=scores))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_COMPLETED_AUCTION_ALREADY_EXTENDED")

    def test_trailing_laggard_requires_transfer_role(self):
        result = self.refine(self.decision(trailing_direction_rank=4))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SEMANTIC_AAC_TRAILING_LAGGARD")

    def test_explicit_laggard_transfer_is_preserved(self):
        result = self.refine(
            self.decision(
                reason=AAC_LAGGARD_TRANSFER,
                event_direction_rank=4,
                trailing_direction_rank=3,
                directional_trend_scores={
                    "BTCUSDT": 0.70,
                    "ETHUSDT": 0.41,
                    "SOLUSDT": 0.64,
                    "XRPUSDT": 0.55,
                },
            ),
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, AAC_LAGGARD_TRANSFER)

    def test_v4_materializer_normalizes_open_time_and_compiles(self):
        source = (ROOT / "run_leadership_scdam_base.py").read_text(encoding="utf-8")
        materialized = materialize_runner_source(source)
        self.assertEqual(materialized.count("candidate-13-strict-open-time"), 1)
        self.assertIn('frame["open_time"] = numeric_open_time.loc[valid_open_time].astype("int64")', materialized)
        compile(materialized, "run_leadership_scdam_base.py", "exec")

    def test_timestamp_contract_drift_fails_closed(self):
        with self.assertRaises(RuntimeError):
            materialize_runner_source("timestamp parser drifted")


if __name__ == "__main__":
    unittest.main()
