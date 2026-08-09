from __future__ import annotations

import inspect
import unittest

import strategy_v46_selective_evidence as strategy
import train_v46_evidence_model as trainer


class V46ContractTest(unittest.TestCase):
    def test_threshold_is_fixed_not_searched(self) -> None:
        self.assertEqual(trainer.THRESHOLD, 0.75)
        source = inspect.getsource(trainer.main)
        self.assertNotIn('for threshold', source)
        self.assertNotIn('grid', source.lower())

    def test_feature_contract_is_shared(self) -> None:
        self.assertGreaterEqual(len(trainer.FEATURE_NAMES), 12)
        self.assertIs(strategy.FEATURE_NAMES, trainer.FEATURE_NAMES)

    def test_numeric_prices_cannot_be_mistaken_for_side(self) -> None:
        self.assertIsNone(strategy._exact_side(30000.0))
        self.assertEqual(strategy._exact_side(1), 1)
        self.assertEqual(strategy._exact_side(-1), -1)

    def test_inherits_v26_execution_and_does_not_change_risk(self) -> None:
        self.assertTrue(issubclass(strategy.SelectiveEvidenceStrategy, strategy._BASE))
        source = inspect.getsource(strategy)
        for token in ('risk_fraction =', 'max_notional', 'leverage_cap', 'match_order', 'calculate_pnl'):
            self.assertNotIn(token, source)


if __name__ == '__main__':
    unittest.main()
