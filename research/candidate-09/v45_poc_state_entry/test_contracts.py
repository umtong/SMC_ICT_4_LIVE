import inspect
import unittest

import run_experiment
import strategy


class Candidate45Contracts(unittest.TestCase):
    def test_v42_state_completes_before_state_entry(self):
        source = inspect.getsource(strategy.Candidate16Strategy._complete_parent)
        parent = source.index("super()._complete_parent(row)")
        poc = source.index("candidate42_poc_migration_pass")
        execution = source.index("POC_VALUE_ACCEPTANCE_ENTRY_EVALUATION")
        submit = source.index("_submit_entry")
        self.assertLess(parent, poc)
        self.assertLess(poc, execution)
        self.assertLess(execution, submit)

    def test_exact_control_changes_only_execution_timing(self):
        configured = inspect.getsource(run_experiment.configured)
        self.assertIn('"candidate42_require_poc_migration": True', configured)
        self.assertIn(
            '"candidate42_min_consecutive_outside_poc_bars": 2',
            configured,
        )
        self.assertIn('"candidate45_enter_on_state_completion"', configured)
        self.assertEqual(
            set(run_experiment.VARIANTS),
            {"state-completion", "first-retest-control"},
        )

    def test_control_retains_first_retest(self):
        source = inspect.getsource(strategy.Candidate16Strategy._complete_parent)
        self.assertIn("candidate45_retest_control_paths", source)
        self.assertIn('"FIRST_BOUNDARY_RETEST"', source)
        self.assertIn('"POC_STATE_COMPLETION_BAR"', source)

    def test_failed_or_blocked_state_cannot_enter(self):
        source = inspect.getsource(strategy.Candidate16Strategy._complete_parent)
        self.assertIn("was_acceptance", source)
        self.assertIn("if not was_acceptance or self.pending is None", source)
        self.assertIn("candidate42_poc_migration_pass", source)
        self.assertIn('self.pending.branch != "ACCEPTANCE"', source)


if __name__ == "__main__":
    unittest.main()
