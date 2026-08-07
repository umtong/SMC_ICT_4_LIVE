from __future__ import annotations

import inspect
import unittest

from nautilus_execution import NautilusExecutionMixin
from nautilus_strategy import _make_scenario_engine, make_strategy_class


class UoamRegistrationContractTests(unittest.TestCase):
    def test_selector_and_context_control_are_registered(self):
        selector = inspect.getsource(_make_scenario_engine)
        strategy_source = inspect.getsource(make_strategy_class)
        execution = inspect.getsource(NautilusExecutionMixin._attempt_entry)
        self.assertIn("UNRESOLVED_OBJECTIVE_LIFECYCLE", selector)
        self.assertIn("_apply_causal_context_control", strategy_source)
        self.assertIn("CAUSAL_CONTEXT_INVALIDATED_BEFORE_ENTRY", strategy_source)
        self.assertIn("causal_exit_reason_codes", execution)
        self.assertIn("causal_exit_open_position", execution)


if __name__ == "__main__":
    unittest.main()
