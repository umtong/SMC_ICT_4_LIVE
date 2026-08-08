import inspect
import unittest

import strategy


class Candidate35Contracts(unittest.TestCase):
    def test_baseline_disables_confirmed_swings(self):
        source = inspect.getsource(strategy.Candidate16Config)
        self.assertIn(
            "candidate35_include_confirmed_swings: bool = False",
            source,
        )

    def test_completed_auction_is_causal(self):
        source = inspect.getsource(strategy.Candidate16Strategy._roll_session)
        self.assertIn("open_minute = ts_ns // _MINUTE_NS - 1", source)
        self.assertIn("completed = (open_minute + 1) % horizon == 0", source)
        self.assertIn("self._add_pool", source)

    def test_pivots_are_exact_single_ablation(self):
        source = inspect.getsource(strategy.Candidate16Strategy._confirm_pivots)
        self.assertIn("candidate35_include_confirmed_swings", source)
        self.assertIn("super()._confirm_pivots", source)


if __name__ == "__main__":
    unittest.main()
