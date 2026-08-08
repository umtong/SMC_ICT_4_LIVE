import inspect
import unittest

import features
import run_experiment
import strategy


class Candidate48Contracts(unittest.TestCase):
    def test_first_ten_seconds_are_exactly_bounded(self):
        source = inspect.getsource(features._first_ten_second_rows)
        self.assertIn("elapsed < 10.0", source)
        self.assertIn("first10_buy_notional", source)
        self.assertIn("first10_sell_notional", source)
        self.assertIn("first10_order_imbalance", source)
        self.assertIn("first10_vwap", source)

    def test_clock_placebo_has_identical_frequency(self):
        self.assertEqual(
            set(run_experiment.VARIANTS),
            {"quarter-hour", "plus-seven-placebo"},
        )
        self.assertEqual(run_experiment.VARIANTS["quarter-hour"], 0)
        self.assertEqual(run_experiment.VARIANTS["plus-seven-placebo"], 7)
        clock = inspect.getsource(strategy.Candidate16Strategy._candidate48_clock_match)
        self.assertIn("open_minute % 15", clock)

    def test_context_state_and_entry_are_separate(self):
        arm = inspect.getsource(strategy.Candidate16Strategy._candidate48_maybe_arm_signal)
        process = inspect.getsource(strategy.Candidate16Strategy._process_pending)
        context = arm.index("first10_order_imbalance")
        state = arm.index("response_pass =")
        armed = arm.index("QUARTER_HOUR_ALGORITHMIC_FLOW_CONFIRMED")
        self.assertLess(context, state)
        self.assertLess(state, armed)
        self.assertNotIn("_submit_entry", arm)
        self.assertIn("QUARTER_HOUR_POC_RETEST", process)
        self.assertIn("_submit_entry", process)

    def test_signal_extreme_and_natural_objective_own_trade_geometry(self):
        arm = inspect.getsource(strategy.Candidate16Strategy._candidate48_maybe_arm_signal)
        process = inspect.getsource(strategy.Candidate16Strategy._process_pending)
        self.assertIn("candidate48_signal_low", arm)
        self.assertIn("candidate48_signal_high", arm)
        self.assertIn("QUARTER_HOUR_SIGNAL_EXTREME_INVALIDATED", process)
        self.assertIn("candidate16_natural_objective_rejections", process)


if __name__ == "__main__":
    unittest.main()
