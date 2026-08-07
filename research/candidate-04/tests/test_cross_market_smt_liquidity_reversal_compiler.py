"""Focused causal tests for candidate-04 V51."""
from __future__ import annotations

import unittest

import pandas as pd

import cross_market_smt_liquidity_reversal_compiler as v51


def frame(rows: list[dict[str, float]]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(rows), freq="min", tz="UTC")
    return pd.DataFrame(rows, index=index)


class V51Tests(unittest.TestCase):
    def test_shifted_quantile_excludes_current_outlier(self) -> None:
        values = pd.Series([1.0] * 720 + [1_000_000.0])
        result = v51.shifted_quantile(values, 0.70)
        self.assertEqual(float(result.iloc[-1]), 1.0)

    def test_peer_asymmetry_counts_rejections_not_common_acceptance(self) -> None:
        rows = [
            {"high": 100.0, "low": 99.0, "ret_60s_bps": 1.0}
            for _ in range(3)
        ]
        frames = {symbol: frame(rows) for symbol in v51.FOLLOWERS}
        edges = {
            symbol: (
                pd.Series([101.0] * 3, index=frames[symbol].index),
                pd.Series([98.0] * 3, index=frames[symbol].index),
            )
            for symbol in v51.FOLLOWERS
        }
        cutoffs = {
            symbol: pd.Series([5.0] * 3, index=frames[symbol].index)
            for symbol in v51.FOLLOWERS
        }
        count, details = v51.peer_rejection_count(
            frames,
            edges,
            2,
            1,
            cutoffs,
        )
        self.assertEqual(count, 3)
        self.assertTrue(
            all(details[symbol]["peer_rejected_sweep"] for symbol in v51.FOLLOWERS)
        )

    def test_common_reversal_requires_index_proxy_and_mss(self) -> None:
        data = frame(
            [
                {
                    "open": 100.0,
                    "close": 100.0,
                    "high": 100.2,
                    "low": 99.8,
                    "atr": 1.0,
                    "ret_60s_bps": 0.0,
                    "flow_60s": 0.0,
                    "basis_change_5m": 0.0,
                },
                {
                    "open": 100.0,
                    "close": 100.0,
                    "high": 100.2,
                    "low": 99.8,
                    "atr": 1.0,
                    "ret_60s_bps": 0.0,
                    "flow_60s": 0.0,
                    "basis_change_5m": 0.0,
                },
                {
                    "open": 100.0,
                    "close": 98.8,
                    "high": 100.1,
                    "low": 98.7,
                    "atr": 1.0,
                    "ret_60s_bps": -12.0,
                    "flow_60s": -0.7,
                    "basis_change_5m": -2.0,
                },
            ]
        )
        passed, _ = v51.common_reversal_confirmation(
            data,
            2,
            -1,
            99.0,
            0.5,
            0.2,
        )
        self.assertTrue(passed)
        data.iloc[2, data.columns.get_loc("basis_change_5m")] = -20.0
        passed, _ = v51.common_reversal_confirmation(
            data,
            2,
            -1,
            99.0,
            0.5,
            0.2,
        )
        self.assertFalse(passed)

    def test_fvg_requires_three_completed_bars_and_later_retest(self) -> None:
        data = frame(
            [
                {"open": 100.0, "close": 100.2, "high": 100.4, "low": 99.8},
                {"open": 100.2, "close": 101.0, "high": 101.1, "low": 100.1},
                {"open": 101.0, "close": 102.0, "high": 102.1, "low": 100.8},
                {"open": 101.2, "close": 101.4, "high": 101.7, "low": 100.5},
            ]
        )
        gap = v51.directional_fvg(data, 2, 1, 0.1)
        self.assertIsNotNone(gap)
        assert gap is not None
        self.assertTrue(v51.fvg_retest_holds(data, 3, 1, gap))


if __name__ == "__main__":
    unittest.main()
