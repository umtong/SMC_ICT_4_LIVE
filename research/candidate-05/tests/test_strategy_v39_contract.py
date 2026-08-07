from __future__ import annotations

import inspect
import unittest

import strategy_v39_dual_auction as v39


class V39ContractTest(unittest.TestCase):
    def test_wraps_real_entry_helpers(self) -> None:
        self.assertTrue(v39._WRAPPED)
        self.assertTrue(all(name.startswith("_submit") for name in v39._WRAPPED))
        self.assertTrue(all("entry" in name for name in v39._WRAPPED))

    def test_inherits_mature_v26_execution(self) -> None:
        self.assertTrue(issubclass(v39.DualAuctionStateStrategy, v39._BASE))
        self.assertIs(v39.CandidateStrategy, v39.DualAuctionStateStrategy)
        self.assertIs(v39.StrategyClass, v39.DualAuctionStateStrategy)

    def test_does_not_override_risk_or_nautilus_accounting(self) -> None:
        source = inspect.getsource(v39)
        forbidden = (
            "risk_fraction =",
            "starting_balance =",
            "max_notional",
            "leverage_cap",
            "custom_backtest",
            "match_order",
            "calculate_pnl",
        )
        for token in forbidden:
            self.assertNotIn(token, source)

    def test_two_states_are_mutually_exclusive_by_oi_sign(self) -> None:
        source = inspect.getsource(v39.DualAuctionStateStrategy._auction_permission)
        self.assertIn("oi_change <= 0.0", source)
        self.assertIn("oi_change > 0.0", source)
        self.assertIn("DELEVERAGING_REVERSAL", source)
        self.assertIn("POSITION_BUILDING_ACCEPTANCE", source)


if __name__ == "__main__":
    unittest.main()
