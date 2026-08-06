from __future__ import annotations

import unittest

import pandas as pd

import rich_signal_compiler_v28 as v28


class V28RouterTests(unittest.TestCase):
    def test_both_independent_refinements_are_active_and_restored(self) -> None:
        original_router = v28.v27.collect_signals
        original_failed = v28.v27.failed_break.collect_signals
        original_directional = v28.v27.directional_session.collect_signals
        observed = {}

        def fake_router(*args, **kwargs):
            del args, kwargs
            observed["failed"] = v28.v27.failed_break.collect_signals
            observed["directional"] = v28.v27.directional_session.collect_signals
            return [], {"router_contract": {"base": "v27"}}

        v28.v27.collect_signals = fake_router
        try:
            intents, summary = v28.collect_signals(
                pd.DataFrame(),
                pd.Timestamp("2024-01-01", tz="UTC"),
                pd.Timestamp("2024-01-02", tz="UTC"),
                object(),
                object(),
                object(),
            )
        finally:
            v28.v27.collect_signals = original_router

        self.assertEqual(intents, [])
        self.assertIs(observed["failed"], v28.impact_tail.collect_signals)
        self.assertIs(
            observed["directional"],
            v28.directional_upper.collect_signals,
        )
        self.assertIs(v28.v27.failed_break.collect_signals, original_failed)
        self.assertIs(
            v28.v27.directional_session.collect_signals,
            original_directional,
        )
        self.assertEqual(summary["candidate"], "candidate-04-v28-auction-state-mosaic")
        self.assertEqual(summary["changes_from_v27"]["changed_state_boundaries"], 2)
        self.assertIn("v28_state_boundaries", summary["router_contract"])


if __name__ == "__main__":
    unittest.main()
