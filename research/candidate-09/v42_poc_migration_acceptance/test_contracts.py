import inspect
import unittest

import run_experiment
import strategy


class Candidate42Contracts(unittest.TestCase):
    def test_poc_migration_requires_consecutive_completed_observations(self):
        config = inspect.getsource(strategy.Candidate16Config)
        router = inspect.getsource(strategy.Candidate16Strategy._router_observation)
        complete = inspect.getsource(strategy.Candidate16Strategy._complete_parent)
        self.assertIn(
            "candidate42_min_consecutive_outside_poc_bars: int = 2",
            config,
        )
        self.assertIn("_candidate42_outside_poc_streak += 1", router)
        self.assertIn("_candidate42_outside_poc_streak = 0", router)
        self.assertIn("candidate42_max_outside_poc_streak", complete)
        self.assertIn("candidate42_terminal_poc_outside", complete)

    def test_price_state_poc_state_and_entry_are_separate(self):
        complete = inspect.getsource(strategy.Candidate16Strategy._complete_parent)
        price_state = complete.index("AuctionDecision.ACCEPTANCE_CONTINUATION")
        poc_state = complete.index("migration_pass =")
        entry_arm = complete.index("_Candidate16V1Strategy._complete_parent")
        self.assertLess(price_state, poc_state)
        self.assertLess(poc_state, entry_arm)
        self.assertIn(
            "TRUE_ACCEPTANCE_WITHOUT_CONSECUTIVE_OUTSIDE_POC_MIGRATION",
            complete,
        )

    def test_failed_and_unresolved_auctions_remain_no_trade(self):
        complete = inspect.getsource(strategy.Candidate16Strategy._complete_parent)
        self.assertIn(
            "state.decision is not AuctionDecision.ACCEPTANCE_CONTINUATION",
            complete,
        )
        self.assertIn("super()._complete_parent(row)", complete)
        self.assertIn("candidate33_trade_failed_auction", inspect.getsource(run_experiment.configured))

    def test_exact_control_removes_only_poc_ownership(self):
        configured = inspect.getsource(run_experiment.configured)
        self.assertIn('"candidate33_require_stacked_imbalance": False', configured)
        self.assertIn('"candidate42_require_poc_migration"', configured)
        self.assertIn('"candidate42_min_consecutive_outside_poc_bars": 2', configured)
        self.assertEqual(
            set(run_experiment.VARIANTS),
            {"poc-migration", "price-only-control"},
        )


if __name__ == "__main__":
    unittest.main()
