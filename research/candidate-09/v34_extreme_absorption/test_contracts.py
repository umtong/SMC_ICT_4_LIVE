import inspect
import unittest

import strategy


class Candidate34Contracts(unittest.TestCase):
    def test_state_and_transition_are_separate(self):
        source = inspect.getsource(strategy.Candidate16Strategy)
        state = source.index("EXTREME_ABSORPTION_FROZEN")
        transition = source.index("OPPOSITE_FOOTPRINT_INITIATIVE_CONFIRMED")
        execution = source.index("candidate34_pullback_entries")
        self.assertLess(state, transition)
        self.assertLess(transition, execution)

    def test_single_ablation_is_explicit(self):
        source = inspect.getsource(strategy.Candidate16Config)
        self.assertIn(
            "candidate34_require_extreme_absorption: bool = True",
            source,
        )

    def test_failed_auction_is_not_immediate_entry(self):
        source = inspect.getsource(strategy.Candidate16Strategy._complete_parent)
        self.assertNotIn("_submit_entry", source)
        self.assertIn("REJECTION_FOOTPRINT_INITIATIVE", source)


if __name__ == "__main__":
    unittest.main()
