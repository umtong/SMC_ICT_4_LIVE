from __future__ import annotations

import unittest

from candidate16_failed_far import SEMANTIC_REJECTED_FAR_REASONS
from market_leadership import LeadershipDecision
from semantic_market_leadership import semantic_decision


class ContestedCountertrendTests(unittest.TestCase):
    def decision(self) -> LeadershipDecision:
        return LeadershipDecision(
            approved=False,
            reason="RAW",
            leader="BTCUSDT",
            symbol="SOLUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=1,
            confirmation_ts_ns=2,
            peer_returns={
                "BTCUSDT": 0.002,
                "ETHUSDT": 0.003,
                "XRPUSDT": 0.001,
            },
            directional_returns={
                "BTCUSDT": -0.02,
                "ETHUSDT": -0.03,
                "SOLUSDT": -0.025,
                "XRPUSDT": -0.01,
            },
            directional_trend_scores={
                "BTCUSDT": -0.7,
                "ETHUSDT": -0.8,
                "SOLUSDT": -0.9,
                "XRPUSDT": -0.6,
            },
            candidate_event_move=0.004,
            peer_event_median=0.002,
            confirmation_impulse=1.5,
            trailing_direction_rank=4,
            event_direction_rank=2,
            event_path_efficiency=0.2,
            event_standardized_displacement=0.8,
        )

    def test_moderate_countertrend_unanimity_is_contested_not_approved(self) -> None:
        result = semantic_decision(
            self.decision(),
            symbol_count=4,
            severe_adverse_trend_score=-1.5,
            minimum_confirmation_impulse=1.0,
            minimum_event_efficiency=0.1,
            minimum_event_displacement=0.5,
        )
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "SEMANTIC_FAR_MODERATE_COUNTERTREND_CONTESTED",
        )

    def test_contested_reason_arms_existing_resolution_watch(self) -> None:
        self.assertIn(
            "SEMANTIC_FAR_MODERATE_COUNTERTREND_CONTESTED",
            SEMANTIC_REJECTED_FAR_REASONS,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
