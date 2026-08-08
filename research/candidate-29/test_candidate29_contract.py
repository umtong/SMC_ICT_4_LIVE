from __future__ import annotations

import inspect
import unittest

from long_strategy import Candidate29Strategy
from long_strategy import CompactEquity
from transmission_strategy import Candidate19Strategy


class Candidate29StorageContractTest(unittest.TestCase):
    def test_equity_sequence_is_writer_compatible(self) -> None:
        equity = CompactEquity()
        self.assertFalse(equity)
        equity.append_raw(1, 100.0)
        equity.append_raw(2, 101.5)
        equity.replace_last(102.0)
        self.assertEqual(len(equity), 2)
        self.assertEqual(equity[-1], {"ts_event": 2, "equity": 102.0})
        self.assertEqual(
            list(equity),
            [
                {"ts_event": 1, "equity": 100.0},
                {"ts_event": 2, "equity": 102.0},
            ],
        )

    def test_only_storage_and_feature_access_are_overridden(self) -> None:
        self.assertTrue(issubclass(Candidate29Strategy, Candidate19Strategy))
        overridden = {
            name
            for name, value in Candidate29Strategy.__dict__.items()
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
            "_detect_sweep",
            "_process_pending",
            "_process_failure_initiative",
            "_process_shock_transmission",
            "_submit_entry",
            "_manage_open_position",
        }
        self.assertTrue(decision_methods.isdisjoint(overridden))


if __name__ == "__main__":
    unittest.main()
