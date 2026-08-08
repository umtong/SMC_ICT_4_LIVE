from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from v13_volatility_squeeze_release_study import SqueezeCandidate
from v13_volatility_squeeze_release_study import aggregate_five_minute
from v13_volatility_squeeze_release_study import collapse_global_clusters
from v13_volatility_squeeze_release_study import detect_candidates
from v13_volatility_squeeze_release_study import score_candidate
from v13_volatility_squeeze_release_study_v2 import utc_time_block


class Candidate16V13SqueezeTests(unittest.TestCase):
    def test_one_minute_input_is_aggregated_to_completed_five_minute_clock(self) -> None:
        index = pd.date_range("2024-01-01T00:00:00Z", periods=10, freq="min").as_unit("ns")
        panel = pd.DataFrame(index=index)
        panel["perp_open"] = np.arange(10, dtype=float) + 100.0
        panel["perp_high"] = panel["perp_open"] + 1.0
        panel["perp_low"] = panel["perp_open"] - 1.0
        panel["perp_close"] = panel["perp_open"] + 0.5
        panel["perp_quote_volume"] = 100.0
        panel["perp_taker_buy_quote"] = 75.0
        panel["spot_open"] = panel["perp_open"]
        panel["spot_high"] = panel["perp_high"]
        panel["spot_low"] = panel["perp_low"]
        panel["spot_close"] = panel["perp_close"]
        panel["spot_quote_volume"] = 100.0
        signal = aggregate_five_minute(panel)
        self.assertEqual(len(signal), 2)
        self.assertEqual(signal.index[0], pd.Timestamp("2024-01-01T00:04:00Z"))
        self.assertEqual(signal.index[1], pd.Timestamp("2024-01-01T00:09:00Z"))
        self.assertAlmostEqual(float(signal.iloc[0]["perp_flow"]), 0.5)

    def _signal(self) -> pd.DataFrame:
        index = pd.date_range("2024-01-01T00:04:00Z", periods=40, freq="5min").as_unit("ns")
        frame = pd.DataFrame(index=index)
        frame["perp_open"] = 99.0
        frame["perp_high"] = 99.5
        frame["perp_low"] = 98.5
        frame["perp_close"] = 99.0
        frame["perp_quote_volume"] = 100.0
        frame["perp_flow"] = 0.0
        frame["spot_return"] = 0.0
        frame["kc_upper"] = 100.1
        frame["kc_lower"] = 97.9
        frame["squeeze_on"] = False
        frame["squeeze_streak"] = 0
        frame["prior_volume_mean"] = 100.0

        # Six completed squeeze bars, ending immediately before release.
        for offset, position in enumerate(range(20, 26), start=1):
            ts = index[position]
            frame.loc[ts, "squeeze_on"] = True
            frame.loc[ts, "squeeze_streak"] = offset
            frame.loc[ts, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
                99.0,
                100.0,
                98.0,
                99.0,
            ]

        release = index[26]
        frame.loc[release, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            99.5,
            100.7,
            99.4,
            100.5,
        ]
        frame.loc[release, "perp_quote_volume"] = 160.0
        frame.loc[release, "perp_flow"] = 0.6
        frame.loc[release, "spot_return"] = 0.002
        frame.loc[release, "kc_upper"] = 100.1

        pullback = index[27]
        frame.loc[pullback, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.5,
            100.6,
            100.05,
            100.3,
        ]
        frame.loc[pullback, "perp_quote_volume"] = 100.0
        frame.loc[pullback, "kc_upper"] = 100.1

        resume = index[28]
        frame.loc[resume, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.3,
            100.8,
            100.2,
            100.7,
        ]
        frame.loc[resume, "perp_flow"] = 0.5
        frame.loc[resume, "spot_return"] = 0.001
        return frame

    def test_state_release_pullback_and_entry_roles_are_separate(self) -> None:
        signal = self._signal()
        candidates = detect_candidates("BTCUSDT", signal)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertLess(candidate.squeeze_end_ts, candidate.release_ts)
        self.assertLess(candidate.release_ts, candidate.pullback_ts)
        self.assertLess(candidate.pullback_ts, candidate.entry_ts)
        self.assertEqual(candidate.target_source, "ONE_SQUEEZE_RANGE_MEASURED_MOVE")
        self.assertAlmostEqual(candidate.target, 102.0)
        self.assertGreaterEqual(candidate.target_net_r, 1.0)

    def test_first_channel_touch_head_fake_is_final(self) -> None:
        signal = self._signal()
        pullback = signal.index[27]
        signal.loc[pullback, "perp_close"] = 99.9
        self.assertEqual(detect_candidates("BTCUSDT", signal), [])

    def test_same_bar_stop_and_measured_target_resolves_to_stop(self) -> None:
        signal = self._signal()
        candidate = detect_candidates("BTCUSDT", signal)[0]
        index = pd.date_range(
            candidate.entry_ts,
            periods=130,
            freq="min",
            tz="UTC",
        ).as_unit("ns")
        panel = pd.DataFrame(index=index)
        panel["perp_open"] = candidate.entry
        panel["perp_high"] = candidate.entry + 0.1
        panel["perp_low"] = candidate.entry - 0.1
        panel["perp_close"] = candidate.entry
        first = candidate.entry_ts + pd.Timedelta(minutes=1)
        panel.loc[first, ["perp_high", "perp_low", "perp_close"]] = [
            candidate.target + 0.1,
            candidate.stop - 0.1,
            candidate.entry,
        ]
        scored = score_candidate(candidate, panel)
        self.assertIsNotNone(scored)
        assert scored is not None
        self.assertEqual(scored.exit_reason, "STOP")
        self.assertAlmostEqual(scored.net_r, -1.0)

    def test_global_cluster_and_time_block_attribution(self) -> None:
        base = detect_candidates("BTCUSDT", self._signal())[0]
        weak = base
        strong = SqueezeCandidate(
            **{
                **{field: getattr(base, field) for field in base.__dataclass_fields__},
                "symbol": "ETHUSDT",
                "entry_ts": base.entry_ts + pd.Timedelta(minutes=2),
                "release_score": base.release_score + 1.0,
            },
        )
        later = SqueezeCandidate(
            **{
                **{field: getattr(base, field) for field in base.__dataclass_fields__},
                "symbol": "SOLUSDT",
                "entry_ts": base.entry_ts + pd.Timedelta(minutes=10),
            },
        )
        selected = collapse_global_clusters([weak, strong, later])
        self.assertEqual([item.symbol for item in selected], ["ETHUSDT", "SOLUSDT"])
        self.assertEqual(utc_time_block(pd.Timestamp("2024-01-01T02:00:00Z")), "ASIA_0000_0759_UTC")
        self.assertEqual(utc_time_block(pd.Timestamp("2024-01-01T15:00:00Z")), "NEW_YORK_1300_2059_UTC")


if __name__ == "__main__":
    unittest.main()
