import inspect
import unittest

import run_experiment
import strategy


class Candidate49Contracts(unittest.TestCase):
    def test_clock_placebo_has_identical_frequency(self):
        self.assertEqual(
            set(run_experiment.VARIANTS),
            {"quarter-hour", "plus-seven-placebo"},
        )
        self.assertEqual(run_experiment.VARIANTS["quarter-hour"], 0)
        self.assertEqual(run_experiment.VARIANTS["plus-seven-placebo"], 7)
        source = inspect.getsource(strategy.Candidate16Strategy._candidate49_clock_match)
        self.assertIn("open_minute % 15", source)

    def test_first10_context_precedes_completed_vwap_retention(self):
        source = inspect.getsource(strategy.Candidate16Strategy._candidate49_maybe_submit)
        context = source.index("first10_order_imbalance")
        state = source.index("retained = direction * (close - first10_vwap)")
        transition = source.index("QUARTER_HOUR_MEDIUM_HORIZON_ENTRY_EVALUATION")
        submit = source.index("_submit_entry")
        self.assertLess(context, state)
        self.assertLess(state, transition)
        self.assertLess(transition, submit)

    def test_signal_bar_owns_invalidation_and_horizon(self):
        source = inspect.getsource(strategy.Candidate16Strategy._candidate49_maybe_submit)
        self.assertIn("signal_low", source)
        self.assertIn("signal_high", source)
        self.assertIn("pool_level=stop_anchor", source)
        self.assertIn("candidate49_horizon_minutes", source)
        configured = inspect.getsource(run_experiment.configured)
        self.assertIn('"max_hold_bars": 240', configured)

    def test_natural_objective_is_mandatory(self):
        source = inspect.getsource(strategy.Candidate16Strategy._candidate49_maybe_submit)
        self.assertIn("candidate16_natural_objective_rejections", source)
        self.assertIn("candidate49_no_natural_objective", source)


if __name__ == "__main__":
    unittest.main()
