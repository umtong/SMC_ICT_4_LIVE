from __future__ import annotations

import unittest

import pandas as pd

import rich_signal_compiler_v29 as v29


class V29RouterTests(unittest.TestCase):
    def test_both_refinements_are_active_and_restored(self) -> None:
        original_router = v29.v28.collect_signals
        original_impact = v29.v28.impact_tail.collect_signals
        original_balanced = v29.v28.v27.balanced_session.collect_signals
        observed = {}

        def fake_router(*args, **kwargs):
            del args, kwargs
            observed["impact"] = v29.v28.impact_tail.collect_signals
            observed["balanced"] = (
                v29.v28.v27.balanced_session.collect_signals
            )
            return [], {"router_contract": {"base": "v28"}}

        v29.v28.collect_signals = fake_router
        try:
            intents, summary = v29.collect_signals(
                pd.DataFrame(),
                pd.Timestamp("2024-01-01", tz="UTC"),
                pd.Timestamp("2024-01-02", tz="UTC"),
                object(),
                object(),
                object(),
            )
        finally:
            v29.v28.collect_signals = original_router

        self.assertEqual(intents, [])
        self.assertIs(observed["impact"], v29.extreme.collect_signals)
        self.assertIs(observed["balanced"], v29.material.collect_signals)
        self.assertIs(v29.v28.impact_tail.collect_signals, original_impact)
        self.assertIs(
            v29.v28.v27.balanced_session.collect_signals,
            original_balanced,
        )
        self.assertEqual(
            summary["candidate"],
            "candidate-04-v29-auction-state-mosaic",
        )
        self.assertEqual(
            summary["changes_from_v28"]["changed_state_boundaries"],
            2,
        )
        self.assertIn("v29_state_boundaries", summary["router_contract"])


if __name__ == "__main__":
    unittest.main()
