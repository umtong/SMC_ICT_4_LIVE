import inspect
import unittest

import run_experiment
import strategy


class Candidate47Contracts(unittest.TestCase):
    def test_apparent_acceptance_owns_no_order(self):
        complete = inspect.getsource(strategy.Candidate16Strategy._complete_parent)
        self.assertIn("AuctionDecision.ACCEPTANCE_CONTINUATION", complete)
        self.assertIn("FAILED_VALUE_MIGRATION_STATE_FROZEN", complete)
        self.assertNotIn("_submit_entry", complete)
        self.assertIn("AWAITING_OLD_BOUNDARY_REACCEPTANCE", complete)

    def test_later_reentry_owns_execution(self):
        process = inspect.getsource(strategy.Candidate16Strategy._process_pending)
        reentry = process.index("reentered =")
        evidence = process.index("poc_inside_old_value")
        transition = process.index("OLD_BOUNDARY_REACCEPTED_WITH_VALUE_RETURN")
        submit = process.index("_submit_entry")
        self.assertLess(reentry, evidence)
        self.assertLess(evidence, transition)
        self.assertLess(transition, submit)
        self.assertIn("directional_footprint_delta", process)

    def test_parent_extreme_remains_invalidation(self):
        complete = inspect.getsource(strategy.Candidate16Strategy._complete_parent)
        process = inspect.getsource(strategy.Candidate16Strategy._process_pending)
        self.assertIn("sweep_extreme=setup.sweep_extreme", complete)
        self.assertIn("APPARENT_ACCEPTANCE_EXTENDED_BEYOND_PARENT_EXTREME", process)
        self.assertIn('setup.branch = "REJECTION"', process)

    def test_exact_control_only_removes_prior_poc_failure(self):
        configured = inspect.getsource(run_experiment.configured)
        self.assertIn('"candidate47_require_poc_migration_failure"', configured)
        self.assertIn('"candidate47_reentry_timeout_bars": 3', configured)
        self.assertEqual(
            set(run_experiment.VARIANTS),
            {"failed-value-migration", "all-reentries-control"},
        )


if __name__ == "__main__":
    unittest.main()
