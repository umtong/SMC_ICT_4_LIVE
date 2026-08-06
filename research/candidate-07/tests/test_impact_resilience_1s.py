from __future__ import annotations

import unittest

import pandas as pd

from diagnose_impact_resilience_1s import (
    ImpactLogic,
    _path_result,
    _pool_confirmations,
)


class ImpactResilienceTests(unittest.TestCase):
    def test_pool_timestamp_never_round_trips_through_float(self) -> None:
        base = 1_766_103_599_999_000_064
        timestamps = pd.Series(
            [base + index * 300_000_000_000 for index in range(5)],
            dtype="int64",
        )
        bars = pd.DataFrame(
            {
                "timestamp_ns": timestamps,
                "high": [100.0, 101.0, 105.0, 102.0, 101.0],
                "low": [95.0, 96.0, 97.0, 96.5, 96.8],
            }
        )
        pools = _pool_confirmations(bars, timeframe="5M", radius=2)
        upper = next(pool for pool in pools if pool.side == "UPPER")
        self.assertEqual(upper.pivot_ts_ns, int(timestamps.iloc[2]))
        self.assertEqual(upper.confirmed_ts_ns, int(timestamps.iloc[4]))

    def test_path_excursion_stops_at_first_terminal_event(self) -> None:
        bars = pd.DataFrame(
            {
                "timestamp_ns": [1, 2, 3, 4],
                "open": [100.0, 100.0, 101.0, 104.0],
                "high": [100.0, 102.0, 106.0, 120.0],
                "low": [100.0, 99.5, 100.5, 103.0],
                "close": [100.0, 101.0, 105.0, 119.0],
            }
        )
        result, terminal_index = _path_result(
            bars,
            start_index=0,
            direction="LONG",
            entry=100.0,
            stop=99.0,
            target=105.0,
            max_hold_seconds=3,
        )
        self.assertEqual(result["outcome"], "TARGET")
        self.assertEqual(terminal_index, 2)
        self.assertEqual(result["mfe_r"], 6.0)
        self.assertLess(result["mfe_r"], 20.0)

    def test_invalid_event_window_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ImpactLogic(event_seconds=2, terminal_seconds=3).validate()


if __name__ == "__main__":
    unittest.main()
