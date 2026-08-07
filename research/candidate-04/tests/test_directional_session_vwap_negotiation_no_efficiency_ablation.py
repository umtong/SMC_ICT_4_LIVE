from __future__ import annotations

import unittest

import pandas as pd

import directional_session_vwap_negotiation_no_efficiency_ablation_compiler as candidate


class NoEfficiencyContextTests(unittest.TestCase):
    @staticmethod
    def state(*, side: int, close: float, vwap: float = 100.0, mad: float = 2.0):
        return candidate.base.parent_base.DirectionalSession(
            session_start=pd.Timestamp("2025-01-01", tz="UTC"),
            high=110.0,
            low=90.0,
            open=100.0,
            close=close,
            vwap=vwap,
            vwap_mad=mad,
            efficiency=0.001,
            past_efficiency_median=0.05,
            side=side,
            directional=False,
        )

    def test_vwap_mad_acceptance_remains_required(self) -> None:
        original = candidate._ORIGINAL_CONTEXTS
        weak_efficiency_but_accepted = self.state(side=1, close=103.0)
        not_value_accepted = self.state(side=1, close=101.0)
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
        state = self.state(side=-1, close=97.0)
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
