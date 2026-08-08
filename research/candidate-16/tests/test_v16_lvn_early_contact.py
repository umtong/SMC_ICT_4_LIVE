from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from v16_lvn_fast_lane_study import FastLane
from v16_lvn_fast_lane_study import NodeCluster
from v16_lvn_fast_lane_study_v2 import strict_detect_lane_candidate


class Candidate16V16EarlyContactTests(unittest.TestCase):
    def test_contact_before_volume_baseline_consumes_lane(self) -> None:
        index = pd.date_range("2024-01-02", periods=1440, freq="min", tz="UTC").as_unit("ns")
        frame = pd.DataFrame(index=index)
        frame["perp_open"] = 99.5
        frame["perp_high"] = 99.6
        frame["perp_low"] = 99.4
        frame["perp_close"] = 99.5
        frame["perp_quote_volume"] = 100.0
        frame["perp_flow"] = 0.0
        frame["spot_ret_1m"] = 0.0
        frame["spot_close"] = 99.5
        lane = FastLane(
            profile_day=date(2024, 1, 1),
            lower_hvn=NodeCluster(10, 12),
            upper_hvn=NodeCluster(20, 22),
            gap_start=13,
            gap_end=19,
            lvn_start=15,
            lvn_end=16,
            lower_hvn_target=99.0,
            lower_entry_edge=100.0,
            upper_entry_edge=101.0,
            upper_hvn_target=102.0,
            row_width=0.1,
            gap_rows=7,
            lvn_rows=2,
            gap_volume_ratio_to_median=0.25,
        )
        early = index[5]
        frame.loc[early, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            99.5,
            100.4,
            99.45,
            100.2,
        ]
        # A later complete textbook chain must still be ignored.
        contact = index[25]
        frame.loc[contact, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            99.5,
            100.6,
            99.45,
            100.5,
        ]
        frame.loc[contact, "perp_quote_volume"] = 250.0
        frame.loc[contact, "perp_flow"] = 0.6
        frame.loc[contact, "spot_ret_1m"] = 0.002
        self.assertIsNone(
            strict_detect_lane_candidate(
                symbol="BTCUSDT",
                frame=frame,
                lane=lane,
            ),
        )


if __name__ == "__main__":
    unittest.main()
