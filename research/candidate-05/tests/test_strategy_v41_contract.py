from __future__ import annotations

import inspect
import unittest

import strategy_v41_competing_auction as v41


class V41ContractTest(unittest.TestCase):
    def test_inherits_v26_execution(self) -> None:
        self.assertTrue(issubclass(v41.CompetingAuctionStrategy, v41._BASE))
        self.assertIs(v41.CandidateStrategy, v41.CompetingAuctionStrategy)

    def test_direction_is_selected_before_inherited_entry(self) -> None:
        source = inspect.getsource(v41.CompetingAuctionStrategy._advance_competing_auction)
        self.assertIn("rejection_ready", source)
        self.assertIn("acceptance_ready", source)
        self.assertIn("_arm_rejection", source)
        self.assertIn("_arm_acceptance", source)

    def test_positioning_states_are_sign_separated(self) -> None:
        source = inspect.getsource(v41.CompetingAuctionStrategy._advance_competing_auction)
        self.assertIn("oi_change <= 0.0", source)
        self.assertIn("oi_change > 0.0", source)

    def test_no_risk_or_matching_engine_override(self) -> None:
        source = inspect.getsource(v41)
        for token in (
            "risk_fraction =",
            "max_notional",
            "leverage_cap",
            "match_order",
            "calculate_pnl",
            "custom_backtest",
        ):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
