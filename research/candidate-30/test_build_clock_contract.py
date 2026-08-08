from __future__ import annotations

import unittest

import pandas as pd

from build_month_v2 import _merge_asof_ns


class Candidate30BuildClockContractTest(unittest.TestCase):
    def test_asof_join_normalizes_mixed_preserved_resolutions(self) -> None:
        left_time = pd.DatetimeIndex(
            pd.to_datetime(
                ["2024-01-01T00:05:00Z", "2024-01-01T00:06:00Z"],
                utc=True,
            ),
        )
        right_time = pd.DatetimeIndex(
            pd.to_datetime(
                ["2024-01-01T00:05:00Z"],
                utc=True,
            ),
        )
        if hasattr(left_time, "as_unit"):
            left_time = left_time.as_unit("ms")
            right_time = right_time.as_unit("us")
        left = pd.DataFrame({"time": left_time, "price": [1.0, 2.0]})
        right = pd.DataFrame(
            {"metrics_observed_time": right_time, "oi": [100.0]},
        )
        joined = _merge_asof_ns(
            left,
            right,
            left_on="time",
            right_on="metrics_observed_time",
            direction="backward",
            allow_exact_matches=True,
        )
        self.assertEqual(list(joined["oi"]), [100.0, 100.0])
        self.assertEqual(str(joined["time"].dtype), "datetime64[ns, UTC]")
        self.assertEqual(
            str(joined["metrics_observed_time"].dtype),
            "datetime64[ns, UTC]",
        )


if __name__ == "__main__":
    unittest.main()
