from __future__ import annotations

import math
import unittest

from candidate_bundle_ml2 import ML2MatureDiagonalResponseFamily
from ml1_model import TradeEconomics
from ml2_model import CatBoostProbabilityModel


class ML2TimeframeContractTests(unittest.TestCase):
    def test_60_minute_bar_is_not_forwarded_to_micro_engine(self) -> None:
        family = ML2MatureDiagonalResponseFamily.__new__(ML2MatureDiagonalResponseFamily)
        family._counts = {}
        self.assertEqual(family.on_bar(60, object()), [])
        self.assertEqual(family._counts["nonmember_timeframe_ignored"], 1)


class ML2UtilityTests(unittest.TestCase):
    def _model(self, probability: float) -> CatBoostProbabilityModel:
        model = CatBoostProbabilityModel.__new__(CatBoostProbabilityModel)
        model.risk_fraction = 0.03
        model.raw_probability = lambda features: probability  # type: ignore[method-assign]
        model.calibrate = lambda raw: raw  # type: ignore[method-assign]
        return model

    @staticmethod
    def _economics() -> TradeEconomics:
        return TradeEconomics(
            planned_risk=1.0,
            planned_reward=1.5,
            gross_rr=1.5,
            win_net_r=1.40,
            loss_net_r=-1.08,
            break_even_probability=1.08 / (1.40 + 1.08),
            entry_fill=100.0,
            target_fill=101.5,
            stop_fill=99.0,
            estimated_win_cost_r=0.10,
            estimated_loss_cost_r=0.08,
        )

    def test_positive_compounding_utility_is_accepted(self) -> None:
        decision = self._model(0.70).decide({}, self._economics())
        self.assertTrue(decision.accepted)
        self.assertGreater(decision.expected_log_growth, 0.0)
        expected = 0.70 * math.log(1.0 + 0.03 * 1.40) + 0.30 * math.log(1.0 - 0.03 * 1.08)
        self.assertAlmostEqual(decision.expected_log_growth, expected)

    def test_negative_compounding_utility_is_rejected(self) -> None:
        decision = self._model(0.30).decide({}, self._economics())
        self.assertFalse(decision.accepted)
        self.assertLessEqual(decision.expected_log_growth, 0.0)


if __name__ == "__main__":
    unittest.main()
