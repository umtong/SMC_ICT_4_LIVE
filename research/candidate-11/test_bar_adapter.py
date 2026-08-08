from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bar_adapter import build_bars


@unittest.skipUnless(importlib.util.find_spec("nautilus_trader"), "pinned Nautilus environment required")
class TestBarAdapter(unittest.TestCase):
    def test_completed_observation_timestamps_are_preserved(self) -> None:
        from nautilus_trader.model.data import BarType
        from nautilus_trader.test_kit.providers import TestInstrumentProvider

        instrument = TestInstrumentProvider.btcusdt_perp_binance()
        bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
        index = pd.DatetimeIndex([pd.Timestamp("2024-08-20 00:01:00", tz="UTC")])
        frame = pd.DataFrame(
            {"open": [60000.0], "high": [60010.0], "low": [59990.0], "close": [60005.0], "volume": [12.345]},
            index=index,
        )
        bars = build_bars(frame, bar_type, instrument)
        self.assertEqual(len(bars), 1)
        self.assertEqual(int(bars[0].ts_event), int(index[0].value))
        self.assertEqual(int(bars[0].ts_init), int(index[0].value))

    def test_non_monotonic_frame_fails_closed(self) -> None:
        from nautilus_trader.model.data import BarType
        from nautilus_trader.test_kit.providers import TestInstrumentProvider

        instrument = TestInstrumentProvider.btcusdt_perp_binance()
        bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
        index = pd.DatetimeIndex([
            pd.Timestamp("2024-08-20 00:02:00", tz="UTC"),
            pd.Timestamp("2024-08-20 00:01:00", tz="UTC"),
        ])
        frame = pd.DataFrame(
            {"open": [1, 1], "high": [2, 2], "low": [0, 0], "close": [1, 1], "volume": [1, 1]},
            index=index,
        )
        with self.assertRaises(ValueError):
            build_bars(frame, bar_type, instrument)


if __name__ == "__main__":
    unittest.main()
