"""Focused causal tests for candidate-04 V55."""
from __future__ import annotations

import unittest

import pandas as pd

import common_factor_negotiation_resolution_compiler as v55


def frame(rows: list[dict[str, float]]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(rows), freq="min", tz="UTC")
    return pd.DataFrame(rows, index=index)


class V55Tests(unittest.TestCase):
    def test_real_counterauction_requires_opposite_price_and_flow_together(self) -> None:
        data = frame(
            [
                {"close": 100.0, "flow_60s": 0.2},
                {"close": 99.5, "flow_60s": -0.4},
                {"close": 99.8, "flow_60s": 0.1},
            ]
        )
        passed, details = v55.has_real_counterauction(data, 1, 3, 1)
        self.assertTrue(passed)
        self.assertEqual(details["counterauction_bar_count"], 1.0)

        data.iloc[1, data.columns.get_loc("flow_60s")] = 0.4
        passed, _ = v55.has_real_counterauction(data, 1, 3, 1)
        self.assertFalse(passed)

    def test_resolution_bar_is_excluded_from_prior_negotiation_range(self) -> None:
        data = frame(
            [
                {"open": 100.0, "high": 100.4, "low": 99.8, "close": 100.1, "atr": 1.0, "ret_60s_bps": 1.0, "flow_60s": 0.2, "basis_change_5m": 0.0},
                {"open": 100.1, "high": 100.3, "low": 99.4, "close": 99.6, "atr": 1.0, "ret_60s_bps": -5.0, "flow_60s": -0.5, "basis_change_5m": 0.0},
                {"open": 99.6, "high": 100.2, "low": 99.5, "close": 100.0, "atr": 1.0, "ret_60s_bps": 4.0, "flow_60s": 0.3, "basis_change_5m": 0.0},
                {"open": 100.0, "high": 101.4, "low": 99.9, "close": 101.2, "atr": 1.0, "ret_60s_bps": 12.0, "flow_60s": 0.8, "basis_change_5m": 1.0},
            ]
        )
        factors = {
            "common_return": pd.Series([0.0, -0.2, 0.1, 0.6], index=data.index),
            "common_flow": pd.Series([0.0, -0.2, 0.1, 0.5], index=data.index),
        }
        passed, details = v55.negotiation_resolution(
            data,
            factors,
            first_retest_index=1,
            resolution_index=3,
            side=1,
            body_cutoff=0.20,
        )
        self.assertTrue(passed)
        self.assertAlmostEqual(details["negotiation_high"], 100.3)
        self.assertLess(details["negotiation_high"], float(data["high"].iloc[3]))
        self.assertEqual(details["entire_prior_negotiation_range_broken"], 1.0)

    def test_resolution_requires_complete_prior_range_break_and_common_factor(self) -> None:
        data = frame(
            [
                {"open": 100.0, "high": 100.4, "low": 99.8, "close": 100.1, "atr": 1.0, "ret_60s_bps": 1.0, "flow_60s": 0.2, "basis_change_5m": 0.0},
                {"open": 100.1, "high": 100.3, "low": 99.4, "close": 99.6, "atr": 1.0, "ret_60s_bps": -5.0, "flow_60s": -0.5, "basis_change_5m": 0.0},
                {"open": 99.6, "high": 100.2, "low": 99.5, "close": 100.0, "atr": 1.0, "ret_60s_bps": 4.0, "flow_60s": 0.3, "basis_change_5m": 0.0},
                {"open": 100.0, "high": 100.25, "low": 99.9, "close": 100.2, "atr": 1.0, "ret_60s_bps": 2.0, "flow_60s": 0.4, "basis_change_5m": 0.0},
            ]
        )
        factors = {
            "common_return": pd.Series([0.0, -0.2, 0.1, 0.5], index=data.index),
            "common_flow": pd.Series([0.0, -0.2, 0.1, 0.4], index=data.index),
        }
        passed, _ = v55.negotiation_resolution(data, factors, 1, 3, 1, 0.10)
        self.assertFalse(passed)

        data.iloc[3, data.columns.get_loc("high")] = 101.2
        data.iloc[3, data.columns.get_loc("close")] = 101.0
        factors["common_return"].iloc[3] = -0.5
        passed, _ = v55.negotiation_resolution(data, factors, 1, 3, 1, 0.10)
        self.assertFalse(passed)

    def test_extension_ablation_does_not_change_negotiation_contract(self) -> None:
        self.assertEqual(v55.MAX_EVENT_EXTENSION_ATR, 2.0)
        self.assertEqual(v55.MIN_NEGOTIATION_BARS, 3)
        self.assertEqual(v55.NEGOTIATION_BARS, 12)
        # The diagnostic ablation is represented only by ``None`` in the caller;
        # the negotiation-resolution function has no extension parameter.
        self.assertNotIn(
            "maximum_event_extension_atr",
            v55.negotiation_resolution.__annotations__,
        )


if __name__ == "__main__":
    unittest.main()
