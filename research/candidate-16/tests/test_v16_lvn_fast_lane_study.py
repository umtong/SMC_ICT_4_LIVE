from __future__ import annotations

import unittest
from datetime import date

import numpy as np
import pandas as pd

from v16_lvn_fast_lane_study import FastLane
from v16_lvn_fast_lane_study import LaneCandidate
from v16_lvn_fast_lane_study import NodeCluster
from v16_lvn_fast_lane_study import calculate_profile
from v16_lvn_fast_lane_study import collapse_global_clusters
from v16_lvn_fast_lane_study import detect_lane_candidate
from v16_lvn_fast_lane_study import score_candidate


class Candidate16V16FastLaneTests(unittest.TestCase):
    def test_profile_finds_thin_gap_between_contiguous_hvn_clusters(self) -> None:
        rows_per_bin = 15
        bins = 100
        closes: list[float] = []
        quote_volumes: list[float] = []
        for index in range(bins):
            center = 100.0 + (index + 0.5) * 0.1
            if 20 <= index <= 22 or 27 <= index <= 29:
                total = 300.0
            elif index in {24, 25}:
                # Two-row bottom-quartile LVN trough.
                total = 3.0
            elif index in {23, 26}:
                # The remainder of the HVN-to-HVN gap remains below profile
                # median but above the LVN quartile.
                total = 10.0
            else:
                total = 5.0 if index % 2 == 0 else 15.0
            for _ in range(rows_per_bin):
                closes.append(center)
                quote_volumes.append(total / rows_per_bin)
        # Trim to a complete UTC day while preserving the two structural peaks.
        closes = closes[:1440]
        quote_volumes = quote_volumes[:1440]
        frame = pd.DataFrame(
            {
                "perp_close": closes,
                "perp_quote_volume": quote_volumes,
            },
        )
        profile = calculate_profile(frame, date(2024, 1, 1))
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertGreaterEqual(len(profile.lanes), 1)
        lane = next(
            value
            for value in profile.lanes
            if value.lower_hvn.start <= 22 and value.upper_hvn.start >= 27
        )
        self.assertGreaterEqual(lane.lower_hvn.rows, 3)
        self.assertGreaterEqual(lane.upper_hvn.rows, 3)
        self.assertLessEqual(lane.gap_rows, 8)
        self.assertLessEqual(lane.lvn_rows, 3)
        self.assertLess(lane.gap_volume_ratio_to_median, 1.0)

    def _lane(self) -> FastLane:
        return FastLane(
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

    def _day(self) -> pd.DataFrame:
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

        contact = index[20]
        frame.loc[contact, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            99.5,
            100.6,
            99.45,
            100.5,
        ]
        frame.loc[contact, "perp_quote_volume"] = 200.0
        frame.loc[contact, "perp_flow"] = 0.6
        frame.loc[contact, "spot_ret_1m"] = 0.002

        retest = index[21]
        frame.loc[retest, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.5,
            100.55,
            99.98,
            100.3,
        ]
        resume = index[22]
        frame.loc[resume, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.3,
            100.8,
            100.25,
            100.7,
        ]
        frame.loc[resume, "perp_flow"] = 0.5
        frame.loc[resume, "spot_ret_1m"] = 0.001
        return frame

    def test_gap_entry_retest_and_resumption_are_separate_roles(self) -> None:
        frame = self._day()
        candidate = detect_lane_candidate(
            symbol="BTCUSDT",
            frame=frame,
            lane=self._lane(),
        )
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertLess(candidate.contact_ts, candidate.retest_ts)
        self.assertLess(candidate.retest_ts, candidate.entry_ts)
        self.assertEqual(candidate.side, 1)
        self.assertEqual(candidate.target_source, "OPPOSITE_PRIOR_DAY_HVN_LOWER_BOUNDARY")
        self.assertAlmostEqual(candidate.target, 102.0)
        self.assertGreaterEqual(candidate.target_net_r, 1.0)

    def test_first_physical_contact_failure_consumes_fresh_lane(self) -> None:
        frame = self._day()
        first = frame.index[20]
        frame.loc[first, "perp_quote_volume"] = 100.0
        # A later apparent contact cannot rescue the lane.
        later = frame.index[30]
        frame.loc[later, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            99.5,
            100.7,
            99.45,
            100.6,
        ]
        frame.loc[later, "perp_quote_volume"] = 250.0
        frame.loc[later, "perp_flow"] = 0.7
        frame.loc[later, "spot_ret_1m"] = 0.002
        self.assertIsNone(
            detect_lane_candidate(
                symbol="BTCUSDT",
                frame=frame,
                lane=self._lane(),
            ),
        )

    def test_same_bar_stop_and_hvn_target_resolves_to_stop(self) -> None:
        frame = self._day()
        candidate = detect_lane_candidate(
            symbol="BTCUSDT",
            frame=frame,
            lane=self._lane(),
        )
        assert candidate is not None
        first = candidate.entry_ts + pd.Timedelta(minutes=1)
        frame.loc[first, ["perp_high", "perp_low", "perp_close"]] = [
            candidate.target + 0.1,
            candidate.stop - 0.1,
            candidate.entry,
        ]
        scored = score_candidate(candidate, frame)
        self.assertIsNotNone(scored)
        assert scored is not None
        self.assertEqual(scored.exit_reason, "STOP")
        self.assertAlmostEqual(scored.net_r, -1.0)

    def test_global_cluster_selects_strongest_traversal(self) -> None:
        base = detect_lane_candidate(
            symbol="BTCUSDT",
            frame=self._day(),
            lane=self._lane(),
        )
        assert base is not None
        values = {field: getattr(base, field) for field in base.__dataclass_fields__}
        weak = base
        strong = LaneCandidate(
            **{
                **values,
                "symbol": "ETHUSDT",
                "entry_ts": base.entry_ts + pd.Timedelta(minutes=2),
                "traversal_score": base.traversal_score + 1.0,
            },
        )
        later = LaneCandidate(
            **{
                **values,
                "symbol": "SOLUSDT",
                "entry_ts": base.entry_ts + pd.Timedelta(minutes=10),
            },
        )
        selected = collapse_global_clusters([weak, strong, later])
        self.assertEqual([item.symbol for item in selected], ["ETHUSDT", "SOLUSDT"])


if __name__ == "__main__":
    unittest.main()
