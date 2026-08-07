from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

import pandas as pd

import features
import timestamp_contract
from shared_account_backtest_v2 import PROJECT_SYMBOLS
from shared_account_backtest_v2 import normalize_equity_files


class SharedDailyNavAlignmentTest(unittest.TestCase):
    def test_direct_shared_runner_installs_common_string_epoch_contract(self) -> None:
        self.assertIs(features.read_kline, timestamp_contract.read_kline)
        values = pd.Series(["1693958400000", "1693958459999"])
        converted = timestamp_contract.epoch_datetime(values)
        self.assertEqual(str(converted.iloc[0]), "2023-09-06 00:00:00+00:00")

    def test_day_close_uses_last_observation_inside_day_not_next_day_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            day1_last = pd.Timestamp("2024-03-01T23:59:59Z").value
            day2_first = pd.Timestamp("2024-03-02T00:00:59Z").value
            for index, symbol in enumerate(PROJECT_SYMBOLS):
                destination = root / "symbols" / symbol
                destination.mkdir(parents=True, exist_ok=True)
                pd.DataFrame(
                    {
                        "ts_event": [day1_last, day2_first],
                        "equity": [110.0 + index, 90.0 + index],
                    },
                ).to_csv(destination / "equity.csv", index=False)

            _, daily, _, _ = normalize_equity_files(
                output=root,
                evaluation_start=date(2024, 3, 1),
                evaluation_end=date(2024, 3, 2),
                starting_nav=100.0,
                ending_nav=90.0,
            )
            # Stable symbol ordering makes XRP the final same-timestamp shared
            # observation. The day-one close is 113, not next-day 90.
            self.assertAlmostEqual(daily["2024-03-01"], 0.13)
            self.assertAlmostEqual(daily["2024-03-02"], 90.0 / 113.0 - 1.0)

    def test_day_without_observation_carries_prior_nav(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            only_day2 = pd.Timestamp("2024-03-02T12:00:00Z").value
            for symbol in PROJECT_SYMBOLS:
                destination = root / "symbols" / symbol
                destination.mkdir(parents=True, exist_ok=True)
                pd.DataFrame(
                    {"ts_event": [only_day2], "equity": [105.0]},
                ).to_csv(destination / "equity.csv", index=False)

            _, daily, _, _ = normalize_equity_files(
                output=root,
                evaluation_start=date(2024, 3, 1),
                evaluation_end=date(2024, 3, 2),
                starting_nav=100.0,
                ending_nav=105.0,
            )
            self.assertEqual(daily["2024-03-01"], 0.0)
            self.assertAlmostEqual(daily["2024-03-02"], 0.05)


if __name__ == "__main__":
    unittest.main()
