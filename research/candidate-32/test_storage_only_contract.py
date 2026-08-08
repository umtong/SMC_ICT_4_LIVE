from __future__ import annotations

import inspect
import unittest

from long_v54_strategy import Candidate32Strategy
from strategy import LiquidityResponseStrategy


class Candidate32StorageOnlyContractTest(unittest.TestCase):
    def test_adapter_does_not_override_any_alpha_or_execution_decision(self) -> None:
        self.assertTrue(issubclass(Candidate32Strategy, LiquidityResponseStrategy))
        overridden = {
            name
            for name, value in Candidate32Strategy.__dict__.items()
            if inspect.isfunction(value)
        }
        self.assertEqual(
            overridden,
            {
                "__init__",
                "_load_features",
                "_advance_features",
                "_features_ready",
                "_feature",
                "_record_equity",
            },
        )
        decision_methods = {
            "on_bar",
            "_detect_sweep",
            "_process_pending",
            "_expire_pending",
            "_advance_failed_inventory_acceptance_watches",
            "_submit_entry",
            "_manage_open_position",
            "_position_size",
        }
        self.assertTrue(decision_methods.isdisjoint(overridden))


if __name__ == "__main__":
    unittest.main()
