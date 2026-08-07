from __future__ import annotations

import unittest

import pandas as pd

import nt_multi_asset_rich_backtest_v44 as candidate


class EvaluationBoundsTests(unittest.TestCase):
    def test_declared_week_becomes_inclusive_utc_nanosecond_bounds(self) -> None:
        start, end = candidate.evaluation_bounds_ns(
            [
                "runner",
                "--evaluation-start",
                "2025-07-21",
                "--evaluation-end",
                "2025-07-27",
            ]
        )
        self.assertEqual(start, int(pd.Timestamp("2025-07-21", tz="UTC").value))
        self.assertEqual(
            end,
            int(
                (
                    pd.Timestamp("2025-07-28", tz="UTC")
                    - pd.Timedelta(nanoseconds=1)
                ).value
            ),
        )

    def test_reversed_bounds_are_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            candidate.evaluation_bounds_ns(
                [
                    "runner",
                    "--evaluation-start=2025-07-28",
                    "--evaluation-end=2025-07-27",
                ]
            )


if __name__ == "__main__":
    unittest.main()
