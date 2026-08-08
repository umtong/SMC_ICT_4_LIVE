from __future__ import annotations

from datetime import date
import unittest

import numpy as np
import pandas as pd

from ny_acceptance import NYAcceptanceConfig, build_levels, first_accepted_break, session_times


class NYAcceptanceTests(unittest.TestCase):
    def _frame(self, local_day: date) -> pd.DataFrame:
        start = pd.Timestamp(local_day, tz="America/New_York").tz_convert("UTC").floor("D") - pd.Timedelta(days=2)
        time = pd.date_range(start, periods=4 * 1440, freq="1min", tz="UTC")
        close = np.full(len(time), 100.0)
        frame = pd.DataFrame({
            "time": time,
            "open": close.copy(), "high": close + 0.02,
            "low": close - 0.02, "close": close.copy(),
        })
        return frame

    def test_dst_session_shift(self) -> None:
        _, _, summer, _ = session_times(date(2025, 9, 10))
        _, _, winter, _ = session_times(date(2025, 12, 10))
        self.assertEqual(summer.hour, 15)
        self.assertEqual(winter.hour, 16)

    def test_three_completed_closes_are_required(self) -> None:
        day = date(2025, 9, 10)
        frame = self._frame(day)
        _, _, trade_start, _ = session_times(day)
        # Give the previous UTC day and pre-NY four hours deterministic ranges.
        previous_midnight = trade_start.floor("D")
        previous = (frame.time >= previous_midnight - pd.Timedelta(days=1)) & (frame.time < previous_midnight)
        frame.loc[previous, ["high", "low"]] = [101.0, 99.0]
        pre_start, pre_end, _, _ = session_times(day)
        pre = (frame.time >= pre_start) & (frame.time < pre_end)
        frame.loc[pre, ["high", "low"]] = [100.5, 99.5]
        levels = build_levels(frame, day)
        level = next(item for item in levels if item.name == "PRE_NY_FOUR_HOUR")
        idx = int(frame.index[frame.time == trade_start][0])
        for offset, value in enumerate((100.56, 100.64, 100.75)):
            frame.loc[idx + offset, ["open", "high", "low", "close"]] = [value - 0.02, value + 0.02, value - 0.03, value]
        frame.loc[idx + 3, "open"] = 100.76
        signal = first_accepted_break(
            symbol="BTCUSDT", frame=frame, local_day=day,
            level=level, config=NYAcceptanceConfig(),
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.signal_index, idx + 2)
        self.assertGreaterEqual(signal.reward_bps / signal.risk_bps, 2.59)

    def test_failed_first_break_is_not_recycled(self) -> None:
        day = date(2025, 9, 10)
        frame = self._frame(day)
        pre_start, pre_end, trade_start, _ = session_times(day)
        pre = (frame.time >= pre_start) & (frame.time < pre_end)
        frame.loc[pre, ["high", "low"]] = [100.5, 99.5]
        level = next(item for item in build_levels(frame, day) if item.name == "PRE_NY_FOUR_HOUR")
        idx = int(frame.index[frame.time == trade_start][0])
        # First break returns inside; a later attractive break must be ignored.
        frame.loc[idx, ["open", "high", "low", "close"]] = [100.49, 100.60, 100.45, 100.49]
        for offset, value in enumerate((100.60, 100.70, 100.80), start=10):
            frame.loc[idx + offset, ["open", "high", "low", "close"]] = [value - 0.02, value + 0.02, value - 0.03, value]
        self.assertIsNone(first_accepted_break(
            symbol="BTCUSDT", frame=frame, local_day=day,
            level=level, config=NYAcceptanceConfig(),
        ))


if __name__ == "__main__":
    unittest.main()
