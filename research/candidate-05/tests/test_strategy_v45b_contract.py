from __future__ import annotations

import inspect
import unittest

import strategy_v45b_hybrid_auction as v45b


class V45BContractTest(unittest.TestCase):
    def test_inherits_hybrid_router(self) -> None:
        self.assertTrue(issubclass(v45b.CorrectedHybridAuctionStrategy, v45b.HybridAuctionRouterStrategy))
        self.assertIs(v45b.CandidateStrategy, v45b.CorrectedHybridAuctionStrategy)

    def test_balance_is_consumed_by_start_time(self) -> None:
        source = inspect.getsource(v45b.CorrectedHybridAuctionStrategy._consume_hybrid)
        self.assertIn('v45b_consumed_balance_starts.add', source)

    def test_directional_pullback_rejection_requires_regime_alignment(self) -> None:
        source = inspect.getsource(v45b.CorrectedHybridAuctionStrategy._arm_rejection)
        self.assertIn('direction == rejection_side', source)
        self.assertIn('regime == "BALANCED"', source)
        self.assertIn('regime == "DIRECTIONAL"', source)

    def test_no_risk_or_accounting_override(self) -> None:
        source = inspect.getsource(v45b)
        for token in ('risk_fraction =', 'max_notional', 'leverage_cap', 'match_order', 'calculate_pnl'):
            self.assertNotIn(token, source)


if __name__ == '__main__':
    unittest.main()
