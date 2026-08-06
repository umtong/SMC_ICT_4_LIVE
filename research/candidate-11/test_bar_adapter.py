from __future__ import annotations

import unittest

import pandas as pd

from bar_adapter import build_bars


class BarAdapterTests(unittest.TestCase):
    def test_builds_official_nautilus_bars_at_observation_time(self) -> None:
        from nautilus_trader.model.data import BarType
        from nautilus_trader.test_kit.providers import TestInstrumentProvider

        instrument = TestInstrumentProvider.btcusdt_perp_binance()
        bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
        index = pd.date_range("2024-01-01T00:01:00Z", periods=2, freq="1min")
        frame = pd.DataFrame(
            {
                "open": [40000.0, 40010.0],
                "high": [40020.0, 40030.0],
                "low": [39990.0, 40000.0],
                "close": [40010.0, 40020.0],
                "volume": [12.345, 23.456],
            },
            index=index,
        )

        bars = build_bars(frame, bar_type, instrument)

        self.assertEqual(len(bars), 2)
        self.assertEqual(int(bars[0].ts_event), int(index[0].value))
        self.assertEqual(int(bars[0].ts_init), int(index[0].value))
        self.assertEqual(str(bars[0].open), "40000.0")
        self.assertEqual(str(bars[0].volume), "12.345")

    def test_rejects_duplicate_observation_timestamps(self) -> None:
        from nautilus_trader.model.data import BarType
        from nautilus_trader.test_kit.providers import TestInstrumentProvider

        instrument = TestInstrumentProvider.btcusdt_perp_binance()
        bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
        timestamp = pd.Timestamp("2024-01-01T00:01:00Z")
        frame = pd.DataFrame(
            {
                "open": [1.0, 1.0], "high": [2.0, 2.0], "low": [0.5, 0.5],
                "close": [1.5, 1.5], "volume": [1.0, 1.0],
            },
            index=pd.DatetimeIndex([timestamp, timestamp]),
        )

        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            build_bars(frame, bar_type, instrument)


if __name__ == "__main__":
    unittest.main()
