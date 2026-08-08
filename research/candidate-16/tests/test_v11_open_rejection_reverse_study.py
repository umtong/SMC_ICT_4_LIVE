from __future__ import annotations

import unittest
from datetime import date

import numpy as np
import pandas as pd

from v11_open_rejection_reverse_study import RejectionCandidate
from v11_open_rejection_reverse_study import ScoredRejection
from v11_open_rejection_reverse_study import ValueProfile
from v11_open_rejection_reverse_study import calculate_value_profile
from v11_open_rejection_reverse_study import collapse_global_clusters
from v11_open_rejection_reverse_study import detect_candidate
from v11_open_rejection_reverse_study import score_candidate
from v11_open_rejection_reverse_study_v2 import records


class Candidate16V11OpenRejectionReverseTests(unittest.TestCase):
    def test_profile_reuses_poc_outward_value_expansion(self) -> None:
        index = pd.date_range("2024-01-01", periods=1440, freq="min", tz="UTC")
        closes = np.concatenate(
            [
                np.full(900, 100.0),
                np.full(300, 101.0),
                np.full(240, 99.0),
            ],
        )
        frame = pd.DataFrame(
            {
                "minute": index,
                "perp_close": closes,
                "perp_quote_volume": np.ones(1440),
            },
        )
        profile = calculate_value_profile(frame, date(2024, 1, 1))
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertLessEqual(profile.val, profile.poc)
        self.assertLessEqual(profile.poc, profile.vah)
        self.assertAlmostEqual(profile.total_quote_volume, 1440.0)
        self.assertAlmostEqual(profile.poc, 100.0, delta=profile.row_width)

    def _panel(self) -> tuple[pd.DataFrame, pd.Timestamp, dict[date, ValueProfile]]:
        start = pd.Timestamp("2024-01-02T08:00:00Z")
        index = pd.date_range(start, periods=120, freq="min", tz="UTC").as_unit("ns")
        frame = pd.DataFrame(index=index)
        frame["minute"] = index
        frame["perp_open"] = 100.6
        frame["perp_high"] = 100.8
        frame["perp_low"] = 100.4
        frame["perp_close"] = 100.6
        frame["perp_flow"] = -0.5
        frame["spot_open"] = 100.6
        frame["spot_high"] = 100.8
        frame["spot_low"] = 100.4
        frame["spot_close"] = 100.6
        frame["spot_ret_1m"] = -0.001

        # First 30 minutes remain outside prior value above VAH=101.
        for offset in range(30):
            ts = index[offset]
            open_price = 102.0 + 0.01 * offset
            close = 102.1 + 0.01 * offset
            frame.loc[ts, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
                102.0 if offset == 0 else open_price,
                close + 0.1,
                close - 0.1,
                close,
            ]
            frame.loc[ts, ["spot_open", "spot_high", "spot_low", "spot_close"]] = [
                open_price,
                close + 0.05,
                close - 0.05,
                close,
            ]
            frame.loc[ts, "perp_flow"] = 0.5
            frame.loc[ts, "spot_ret_1m"] = 0.001

        # First second-period bar re-enters value with spot and flow confirmation.
        reentry = index[30]
        frame.loc[reentry, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            101.3,
            101.35,
            100.7,
            100.8,
        ]
        frame.loc[reentry, "perp_flow"] = -0.7
        frame.loc[reentry, "spot_ret_1m"] = -0.002

        # First later counter-direction pullback, then strictly later resumption.
        pullback = index[31]
        frame.loc[pullback, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.8,
            101.0,
            100.7,
            100.9,
        ]
        frame.loc[pullback, "perp_flow"] = 0.2
        frame.loc[pullback, "spot_ret_1m"] = 0.0005
        resume = index[32]
        frame.loc[resume, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.9,
            100.95,
            100.5,
            100.6,
        ]
        frame.loc[resume, "perp_flow"] = -0.6
        frame.loc[resume, "spot_ret_1m"] = -0.0015

        profile = ValueProfile(
            day=date(2024, 1, 1),
            low=98.0,
            high=102.0,
            row_width=0.1,
            poc=99.5,
            val=98.5,
            vah=101.0,
            total_quote_volume=1_000_000.0,
        )
        return frame, start, {date(2024, 1, 1): profile}

    def test_reentry_is_state_only_and_entry_requires_later_leg(self) -> None:
        panel, start, profiles = self._panel()
        candidate = detect_candidate(
            symbol="BTCUSDT",
            panel=panel,
            profiles=profiles,
            session_name="EUROPE_0800_UTC",
            session_ts=start,
        )
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.reentry_ts, start + pd.Timedelta(minutes=30))
        self.assertEqual(candidate.entry_ts, start + pd.Timedelta(minutes=32))
        self.assertGreater(candidate.entry_ts, candidate.reentry_ts)
        self.assertEqual(candidate.side, -1)
        self.assertAlmostEqual(candidate.target, 99.5)
        self.assertGreaterEqual(candidate.target_net_r, 1.0)

    def test_same_bar_stop_and_poc_target_resolves_to_stop(self) -> None:
        panel, start, _ = self._panel()
        candidate = RejectionCandidate(
            symbol="BTCUSDT",
            session="EUROPE_0800_UTC",
            session_open_ts=start,
            reentry_ts=start + pd.Timedelta(minutes=30),
            entry_ts=start + pd.Timedelta(minutes=32),
            side=-1,
            entry=100.6,
            stop=101.1,
            target=99.5,
            target_source="PRIOR_DAY_VOLUME_POC",
            planned_loss_rate=(101.1 - 100.6) / 100.6 + 0.002,
            target_net_r=1.2,
            profile_day="2024-01-01",
            profile_poc=99.5,
            profile_val=98.5,
            profile_vah=101.0,
            profile_row_width=0.1,
            open_price=102.0,
            outside_extension=1.5,
            reentry_price=100.8,
            rejection_score=10.0,
        )
        first = candidate.entry_ts + pd.Timedelta(minutes=1)
        panel.loc[first, ["perp_high", "perp_low", "perp_close"]] = [101.2, 99.4, 100.0]
        scored = score_candidate(candidate, panel)
        self.assertIsNotNone(scored)
        assert scored is not None
        self.assertEqual(scored.exit_reason, "STOP")
        self.assertAlmostEqual(scored.net_r, -1.0)

    def test_global_cluster_keeps_strongest_rejection_only(self) -> None:
        start = pd.Timestamp("2024-01-02T09:02:00Z")
        common = dict(
            session="EUROPE_0800_UTC",
            session_open_ts=start - pd.Timedelta(minutes=62),
            reentry_ts=start - pd.Timedelta(minutes=2),
            side=-1,
            entry=100.0,
            stop=101.0,
            target=98.0,
            target_source="PRIOR_DAY_VOLUME_POC",
            planned_loss_rate=0.012,
            target_net_r=1.5,
            profile_day="2024-01-01",
            profile_poc=98.0,
            profile_val=97.0,
            profile_vah=101.0,
            profile_row_width=0.1,
            open_price=102.0,
            outside_extension=1.0,
            reentry_price=100.5,
        )
        weak = RejectionCandidate(symbol="BTCUSDT", entry_ts=start, rejection_score=1.0, **common)
        strong = RejectionCandidate(
            symbol="ETHUSDT",
            entry_ts=start + pd.Timedelta(minutes=2),
            rejection_score=2.0,
            **common,
        )
        later = RejectionCandidate(
            symbol="SOLUSDT",
            entry_ts=start + pd.Timedelta(minutes=10),
            rejection_score=1.5,
            **common,
        )
        selected = collapse_global_clusters([weak, strong, later])
        self.assertEqual([item.symbol for item in selected], ["ETHUSDT", "SOLUSDT"])

    def test_slots_evidence_serialization_uses_asdict(self) -> None:
        candidate = RejectionCandidate(
            symbol="BTCUSDT",
            session="EUROPE_0800_UTC",
            session_open_ts=pd.Timestamp("2024-01-02T08:00:00Z"),
            reentry_ts=pd.Timestamp("2024-01-02T08:30:00Z"),
            entry_ts=pd.Timestamp("2024-01-02T08:32:00Z"),
            side=-1,
            entry=100.0,
            stop=101.0,
            target=98.0,
            target_source="PRIOR_DAY_VOLUME_POC",
            planned_loss_rate=0.012,
            target_net_r=1.5,
            profile_day="2024-01-01",
            profile_poc=98.0,
            profile_val=97.0,
            profile_vah=101.0,
            profile_row_width=0.1,
            open_price=102.0,
            outside_extension=1.0,
            reentry_price=100.5,
            rejection_score=1.0,
        )
        scored = ScoredRejection(
            candidate=candidate,
            exit_ts=pd.Timestamp("2024-01-02T08:40:00Z"),
            exit_reason="POC_TARGET",
            exit_price=98.0,
            net_return=0.018,
            net_r=1.5,
            mfe=0.02,
            mae=-0.002,
        )
        frame = records([scored])
        self.assertEqual(frame.iloc[0]["symbol"], "BTCUSDT")
        self.assertEqual(frame.iloc[0]["exit_reason"], "POC_TARGET")


if __name__ == "__main__":
    unittest.main()
