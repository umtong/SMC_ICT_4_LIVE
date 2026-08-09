from __future__ import annotations

import inspect
import unittest

import strategy_v44_regime_auction as v44


class V44ContractTest(unittest.TestCase):
    def test_inherits_competing_auction(self) -> None:
        self.assertTrue(issubclass(v44.RegimeConditionedAuctionStrategy, v44.CompetingAuctionStrategy))
        self.assertIs(v44.CandidateStrategy, v44.RegimeConditionedAuctionStrategy)

    def test_natural_regime_boundaries(self) -> None:
        self.assertAlmostEqual(v44.RegimeConditionedAuctionStrategy.BALANCED_MAX_EFFICIENCY, 1.0 / 3.0)
        self.assertAlmostEqual(v44.RegimeConditionedAuctionStrategy.DIRECTIONAL_MIN_EFFICIENCY, 1.0 / 2.0)

    def test_regime_routes_rejection_and_acceptance(self) -> None:
        reject = inspect.getsource(v44.RegimeConditionedAuctionStrategy._arm_rejection)
        accept = inspect.getsource(v44.RegimeConditionedAuctionStrategy._arm_acceptance)
        self.assertIn('BALANCED', reject)
        self.assertIn('DIRECTIONAL', accept)
        self.assertIn('direction != watch.sweep_direction', accept)

    def test_no_risk_or_accounting_override(self) -> None:
        source = inspect.getsource(v44)
        for token in ('risk_fraction =', 'max_notional', 'leverage_cap', 'match_order', 'calculate_pnl'):
            self.assertNotIn(token, source)


if __name__ == '__main__':
    unittest.main()
