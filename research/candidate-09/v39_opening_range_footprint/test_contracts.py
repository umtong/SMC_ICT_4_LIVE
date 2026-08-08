import inspect
import unittest

import strategy


class Candidate39Contracts(unittest.TestCase):
    def test_baseline_is_first_fifteen_minutes(self):
        source = inspect.getsource(strategy.Candidate16Config)
        self.assertIn("candidate39_range_minutes: int = 15", source)
        self.assertIn("candidate39_range_offset_minutes: int = 0", source)
        self.assertIn("candidate39_cycle_minutes: int = _CYCLE_MINUTES", source)

    def test_range_completion_is_causal(self):
        source = inspect.getsource(strategy.Candidate16Strategy._roll_session)
        self.assertIn("open_minute = ts_ns // _MINUTE_NS - 1", source)
        self.assertIn("open_minute != range_end", source)
        self.assertIn("candidate39_range_minutes", source)
        self.assertIn("_candidate39_add_trigger_pool", source)

    def test_only_range_edges_can_open_parent_auction(self):
        source = inspect.getsource(strategy.Candidate16Strategy._detect_sweep)
        self.assertIn("_candidate39_trigger_session", source)
        self.assertIn("self.active_pools = dict(eligible)", source)
        self.assertIn("super()._detect_sweep", source)
        self.assertIn("OPENING_RANGE_PARENT_INTERACTION_ALREADY_RESOLVED", source)

    def test_control_changes_only_range_timing(self):
        source = inspect.getsource(strategy.Candidate16Strategy.__init__)
        self.assertIn("candidate39_range_offset_minutes not in (0, 15)", source)
        self.assertIn("v39 permits only the opening range or its exact +15m control", source)


if __name__ == "__main__":
    unittest.main()
