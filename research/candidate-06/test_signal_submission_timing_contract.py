from __future__ import annotations

import inspect
import unittest

from nautilus_strategy import make_strategy_class
from run_auction_migration_matrix import _base


class SignalSubmissionTimingTests(unittest.TestCase):
    def test_aimd_declares_signal_close_submission(self):
        config = _base({"logic": {}, "execution": {}, "validation": {}, "gate": {}})
        self.assertEqual(config["logic"]["signal_submission_timing"], "ON_SIGNAL_CLOSE")

    def test_strategy_keeps_legacy_default_and_explicit_immediate_path(self):
        _, strategy = make_strategy_class()
        source = inspect.getsource(strategy.on_bar)
        self.assertIn('"NEXT_COMPLETED_BAR"', source)
        self.assertIn('timing == "ON_SIGNAL_CLOSE"', source)
        self.assertIn('self._attempt_entry(step.signal, snapshot)', source)
        self.assertLess(
            source.index('timing == "ON_SIGNAL_CLOSE"'),
            source.index('self._pending_signal = step.signal'),
        )

    def test_no_signal_can_submit_before_scenario_observation(self):
        _, strategy = make_strategy_class()
        source = inspect.getsource(strategy.on_bar)
        self.assertLess(
            source.index('step = self._scenario_engine.observe(snapshot, allow_new=True)'),
            source.index('self._attempt_entry(step.signal, snapshot)'),
        )
        self.assertLess(
            source.index('self._record_transitions(step.transitions, ts_ns)'),
            source.index('self._attempt_entry(step.signal, snapshot)'),
        )


if __name__ == "__main__":
    unittest.main()
