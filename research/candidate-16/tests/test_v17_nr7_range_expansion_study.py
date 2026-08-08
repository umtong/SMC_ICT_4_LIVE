from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from v17_nr7_range_expansion_study import DailyRangeState
from v17_nr7_range_expansion_study import NR7Candidate
from v17_nr7_range_expansion_study import build_daily_states
from v17_nr7_range_expansion_study import collapse_global_clusters
from v17_nr7_range_expansion_study import detect_candidate
from v17_nr7_range_expansion_study import score_candidate


class Candidate16V17NR7Tests(unittest.TestCase):
    def test_daily_state_marks_current_day_only_after_seven_completed_days(self) -> None:
        index = pd.date_range("2024-01-01", periods=8 * 1440, freq="min", tz="UTC").as_unit("ns")
        panel = pd.DataFrame(index=index)
        panel["perp_open"] = 100.0
        panel["perp_close"] = 100.0
        panel["perp_high"] = 101.0
        panel["perp_low"] = 99.0
        # Day 7 is the narrowest completed range of the trailing seven days.
        day7 = pd.date_range("2024-01-07", periods=1440, freq="min", tz="UTC").as_unit("ns")
        panel.loc[day7, "perp_high"] = 100.4
        panel.loc[day7, "perp_low"] = 99.6
        states = build_daily_states(panel)
        self.assertFalse(states[date(2024, 1, 6)].nr7)
        self.assertTrue(states[date(2024, 1, 7)].nr7)
        self.assertAlmostEqual(states[date(2024, 1, 7)].normalized_range, 0.008)

    def _state(self) -> DailyRangeState:
        return DailyRangeState(
            day=date(2024, 1, 1),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            normalized_range=0.02,
            nr7=True,
        )

    def _next_day(self) -> pd.DataFrame:
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

        contact = index[20]
        frame.loc[contact, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.0,
            101.5,
            99.95,
            101.4,
        ]
        frame.loc[contact, "perp_quote_volume"] = 200.0
        frame.loc[contact, "perp_flow"] = 0.6
        frame.loc[contact, "spot_ret_1m"] = 0.002

        retest = index[21]
        frame.loc[retest, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            101.4,
            101.45,
            100.99,
            101.3,
        ]
        resume = index[22]
        frame.loc[resume, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            101.3,
            101.75,
            101.25,
            101.6,
        ]
        frame.loc[resume, "perp_flow"] = 0.5
        frame.loc[resume, "spot_ret_1m"] = 0.001
        return frame

    def test_first_breakout_retest_and_later_resumption_are_separate(self) -> None:
        frame = self._next_day()
        candidate = detect_candidate(
            symbol="BTCUSDT",
            state=self._state(),
            next_day=frame,
        )
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertLess(candidate.contact_ts, candidate.retest_ts)
        self.assertLess(candidate.retest_ts, candidate.entry_ts)
        self.assertEqual(candidate.side, 1)
        self.assertEqual(candidate.target_source, "ONE_PRIOR_NR7_DAY_RANGE_EXTENSION")
        self.assertAlmostEqual(candidate.target, 103.0)
        self.assertGreaterEqual(candidate.target_net_r, 1.0)

    def test_first_contact_failure_consumes_both_nr7_boundaries(self) -> None:
        frame = self._next_day()
        first = frame.index[20]
        frame.loc[first, "perp_quote_volume"] = 100.0
        # A later opposite-side valid-looking breakdown cannot be selected.
        later = frame.index[40]
        frame.loc[later, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.0,
            100.05,
            98.5,
            98.6,
        ]
        frame.loc[later, "perp_quote_volume"] = 250.0
        frame.loc[later, "perp_flow"] = -0.7
        frame.loc[later, "spot_ret_1m"] = -0.002
        self.assertIsNone(
            detect_candidate(
                symbol="BTCUSDT",
                state=self._state(),
                next_day=frame,
            ),
        )

    def test_two_sided_first_contact_is_unresolved(self) -> None:
        frame = self._next_day()
        first = frame.index[20]
        frame.loc[first, ["perp_high", "perp_low"]] = [101.2, 98.8]
        self.assertIsNone(
            detect_candidate(
                symbol="BTCUSDT",
                state=self._state(),
                next_day=frame,
            ),
        )

    def test_same_bar_stop_and_measured_target_resolves_to_stop(self) -> None:
        frame = self._next_day()
        candidate = detect_candidate(
            symbol="BTCUSDT",
            state=self._state(),
            next_day=frame,
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

    def test_global_cluster_keeps_strongest_expansion(self) -> None:
        base = detect_candidate(
            symbol="BTCUSDT",
            state=self._state(),
            next_day=self._next_day(),
        )
        assert base is not None
        values = {field: getattr(base, field) for field in base.__dataclass_fields__}
        strong = NR7Candidate(
            **{
                **values,
                "symbol": "ETHUSDT",
                "entry_ts": base.entry_ts + pd.Timedelta(minutes=2),
                "expansion_score": base.expansion_score + 1.0,
            },
        )
        later = NR7Candidate(
            **{
                **values,
                "symbol": "SOLUSDT",
                "entry_ts": base.entry_ts + pd.Timedelta(minutes=10),
            },
        )
        selected = collapse_global_clusters([base, strong, later])
        self.assertEqual([item.symbol for item in selected], ["ETHUSDT", "SOLUSDT"])


if __name__ == "__main__":
    unittest.main()
