from __future__ import annotations

from types import SimpleNamespace
import unittest

import pandas as pd

import directional_session_vwap_negotiation_no_efficiency_ablation_compiler as candidate


class NoEfficiencyContextTests(unittest.TestCase):
    def test_vwap_mad_acceptance_remains_required(self) -> None:
        original = candidate._ORIGINAL_CONTEXTS
        weak_efficiency_but_accepted = SimpleNamespace(
            side=1,
            close=103.0,
            vwap=100.0,
            vwap_mad=2.0,
            directional=False,
        )
        not_value_accepted = SimpleNamespace(
            side=1,
            close=101.0,
            vwap=100.0,
            vwap_mad=2.0,
            directional=False,
        )
        try:
            candidate._ORIGINAL_CONTEXTS = lambda data: {
                pd.Timestamp("2025-01-01", tz="UTC"): weak_efficiency_but_accepted,
                pd.Timestamp("2025-01-02", tz="UTC"): not_value_accepted,
            }
            values = candidate.contexts_without_efficiency_cutoff(pd.DataFrame())
        finally:
            candidate._ORIGINAL_CONTEXTS = original
        keys = sorted(values)
        self.assertTrue(values[keys[0]].directional)
        self.assertFalse(values[keys[1]].directional)

    def test_ablation_does_not_change_side(self) -> None:
        original = candidate._ORIGINAL_CONTEXTS
        state = SimpleNamespace(
            side=-1,
            close=97.0,
            vwap=100.0,
            vwap_mad=2.0,
            directional=False,
        )
        try:
            candidate._ORIGINAL_CONTEXTS = lambda data: {
                pd.Timestamp("2025-01-01", tz="UTC"): state,
            }
            values = candidate.contexts_without_efficiency_cutoff(pd.DataFrame())
        finally:
            candidate._ORIGINAL_CONTEXTS = original
        item = next(iter(values.values()))
        self.assertEqual(item.side, -1)
        self.assertTrue(item.directional)


if __name__ == "__main__":
    unittest.main()
