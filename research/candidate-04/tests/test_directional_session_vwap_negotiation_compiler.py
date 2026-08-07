from __future__ import annotations

import math
from types import SimpleNamespace
import unittest

import pandas as pd

import directional_session_vwap_negotiation_compiler as candidate


class DirectionalSessionNegotiationTests(unittest.TestCase):
    def test_counterauction_requires_counter_side_close_flow_and_return(self) -> None:
        row = pd.Series({"close": 99.0, "flow_60s": -0.2, "ret_60s_bps": -3.0})
        self.assertTrue(candidate.counterauction_bar(row, 100.0, 1))
        self.assertFalse(
            candidate.counterauction_bar(
                pd.Series({"close": 101.0, "flow_60s": -0.2, "ret_60s_bps": -3.0}),
                100.0,
                1,
            )
        )
        self.assertFalse(
            candidate.counterauction_bar(
                pd.Series({"close": 99.0, "flow_60s": 0.2, "ret_60s_bps": -3.0}),
                100.0,
                1,
            )
        )

    def test_negotiation_break_uses_prior_completed_close_range(self) -> None:
        prior = pd.Series([99.0, 100.0, 100.5])
        self.assertTrue(candidate.negotiation_break(prior, 101.0, 1))
        self.assertFalse(candidate.negotiation_break(prior, 100.5, 1))
        self.assertTrue(candidate.negotiation_break(prior, 98.0, -1))

    def test_liquidation_requires_material_contraction_and_no_rebuild(self) -> None:
        passed, details = candidate.liquidation_cleared(
            1000.0,
            990.0,
            995.0,
            0.005,
        )
        self.assertTrue(passed)
        self.assertTrue(details["material_liquidation_occurred"])
        self.assertTrue(details["liquidation_not_rebuilt"])
        rebuilt, _ = candidate.liquidation_cleared(
            1000.0,
            990.0,
            1001.0,
            0.005,
        )
        self.assertFalse(rebuilt)
        immaterial, _ = candidate.liquidation_cleared(
            1000.0,
            997.0,
            998.0,
            0.005,
        )
        self.assertFalse(immaterial)

    def test_parent_target_is_directional_and_preexisting(self) -> None:
        long_parent = SimpleNamespace(
            side=1,
            high=110.0,
            low=90.0,
            session_start=pd.Timestamp("2025-01-01", tz="UTC"),
        )
        short_parent = SimpleNamespace(
            side=-1,
            high=110.0,
            low=90.0,
            session_start=pd.Timestamp("2025-01-01", tz="UTC"),
        )
        long_target, long_source = candidate.parent_target(long_parent)
        short_target, short_source = candidate.parent_target(short_parent)
        self.assertEqual(long_target, 110.0)
        self.assertTrue(long_source.endswith("_high"))
        self.assertEqual(short_target, 90.0)
        self.assertTrue(short_source.endswith("_low"))

    def test_target_must_remain_untouched_through_signal(self) -> None:
        data = pd.DataFrame(
            {
                "high": [101.0, 102.0, 103.0],
                "low": [99.0, 98.0, 97.0],
            }
        )
        self.assertTrue(candidate.target_is_unconsumed(data, 0, 2, 1, 104.0))
        self.assertFalse(candidate.target_is_unconsumed(data, 0, 2, 1, 103.0))
        self.assertTrue(candidate.target_is_unconsumed(data, 0, 2, -1, 96.0))
        self.assertFalse(candidate.target_is_unconsumed(data, 0, 2, -1, 97.0))

    def test_nonfinite_liquidation_evidence_fails_closed(self) -> None:
        passed, details = candidate.liquidation_cleared(
            1000.0,
            math.nan,
            995.0,
            0.005,
        )
        self.assertFalse(passed)
        self.assertEqual(details, {})


if __name__ == "__main__":
    unittest.main()
