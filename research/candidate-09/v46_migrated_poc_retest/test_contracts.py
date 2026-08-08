import inspect
import unittest

import run_experiment
import strategy


class Candidate46Contracts(unittest.TestCase):
    def test_v42_state_precedes_migrated_poc_retest(self):
        complete = inspect.getsource(strategy.Candidate16Strategy._complete_parent)
        state = complete.index("super()._complete_parent(row)")
        poc = complete.index("candidate42_terminal_poc")
        arm = complete.index("MIGRATED_POC_RETEST_ARMED")
        self.assertLess(state, poc)
        self.assertLess(poc, arm)

    def test_old_boundary_keeps_invalidation_and_stop_ownership(self):
        complete = inspect.getsource(strategy.Candidate16Strategy._complete_parent)
        process = inspect.getsource(strategy.Candidate16Strategy._process_pending)
        self.assertIn('"candidate46_old_boundary": self.pending.pool_level', complete)
        self.assertIn("OLD_AUCTION_BOUNDARY_REACCEPTED", process)
        self.assertIn("setup.pool_level = old_boundary", process)
        self.assertIn("return self._submit_entry(setup, row)", process)

    def test_retest_targets_new_value_not_old_boundary(self):
        process = inspect.getsource(strategy.Candidate16Strategy._process_pending)
        self.assertIn("candidate46_migrated_poc", process)
        self.assertIn("touched =", process)
        self.assertIn("held_new_value", process)
        self.assertIn("candidate46_poc_retest_entries", process)

    def test_exact_control_only_changes_retest_location(self):
        configured = inspect.getsource(run_experiment.configured)
        self.assertIn('"candidate42_require_poc_migration": True', configured)
        self.assertIn('"candidate46_retest_migrated_poc"', configured)
        self.assertEqual(
            set(run_experiment.VARIANTS),
            {"migrated-poc-retest", "old-boundary-control"},
        )


if __name__ == "__main__":
    unittest.main()
