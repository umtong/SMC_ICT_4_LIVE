from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from v17_nr7_range_expansion_study import DailyRangeState
from v17_nr7_range_expansion_study_v2 import strict_detect_candidate


class Candidate16V17EarlyContactTests(unittest.TestCase):
    def test_contact_before_volume_baseline_consumes_nr7_state(self) -> None:
        state = DailyRangeState(
            day=date(2024, 1, 1),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            normalized_range=0.02,
            nr7=True,
        )
        index = pd.date_range("2024-01-02", periods=1440, freq="min", tz="UTC").as_unit("ns")
        frame = pd.DataFrame(index=index)
        frame["perp_open"] = 100.0
        frame["perp_high"] = 100.1
        frame["perp_low"] = 99.9
        frame["perp_close"] = 100.0
        frame["perp_quote_volume"] = 100.0
        frame["perp_flow"] = 0.0
        frame["spot_ret_1m"] = 0.0
        frame["spot_close"] = 100.0
        early = index[5]
        frame.loc[early, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.0,
            101.2,
            99.95,
            101.1,
        ]
        # Later conditions do not matter once the state was consumed.
        later = index[25]
        frame.loc[later, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.0,
            101.5,
            99.95,
            101.4,
        ]
        frame.loc[later, "perp_quote_volume"] = 250.0
        frame.loc[later, "perp_flow"] = 0.7
        frame.loc[later, "spot_ret_1m"] = 0.002
        self.assertIsNone(
            strict_detect_candidate(
                symbol="BTCUSDT",
                state=state,
                next_day=frame,
            ),
        )


if __name__ == "__main__":
    unittest.main()
