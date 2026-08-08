from __future__ import annotations

import unittest
from datetime import date

import numpy as np
import pandas as pd

from v10_open_drive_study import CandidateTrade
from v10_open_drive_study import SessionOpen
from v10_open_drive_study import ThresholdHistory
from v10_open_drive_study import _collapse_entry_clusters
from v10_open_drive_study import _directional_target
from v10_open_drive_study import _ny_open
from v10_open_drive_study import detect_candidate
from v10_open_drive_study import score_candidate


class Candidate16V10OpenDriveTests(unittest.TestCase):
    def test_new_york_open_uses_actual_dst(self) -> None:
        self.assertEqual(str(_ny_open(date(2024, 1, 3))), "2024-01-03 14:30:00+00:00")
        self.assertEqual(str(_ny_open(date(2024, 7, 3))), "2024-07-03 13:30:00+00:00")

    def test_directional_target_is_nearest_past_known_liquidity(self) -> None:
        self.assertEqual(
            _directional_target(
                side=1,
                entry=100.0,
                prior_4h_high=102.0,
                prior_4h_low=98.0,
                prior_24h_high=105.0,
                prior_24h_low=95.0,
            ),
            (102.0, "PRIOR_4H_HIGH"),
        )
        self.assertEqual(
            _directional_target(
                side=-1,
                entry=100.0,
                prior_4h_high=102.0,
                prior_4h_low=98.0,
                prior_24h_high=105.0,
                prior_24h_low=95.0,
            ),
            (98.0, "PRIOR_4H_LOW"),
        )

    def _long_open_drive_panel(self) -> tuple[pd.DataFrame, pd.Timestamp]:
        start = pd.Timestamp("2024-01-02T08:00:00Z")
        index = pd.date_range(start, periods=100, freq="min", tz="UTC").as_unit("ns")
        frame = pd.DataFrame(index=index)
        frame["minute"] = index
        frame["perp_open"] = 101.2
        frame["perp_high"] = 101.3
        frame["perp_low"] = 101.0
        frame["perp_close"] = 101.2
        frame["perp_quote_volume"] = 100.0
        frame["perp_taker_buy_quote"] = 75.0
        frame["spot_open"] = 101.2
        frame["spot_high"] = 101.3
        frame["spot_low"] = 101.0
        frame["spot_close"] = 101.2
        frame["spot_quote_volume"] = 100.0
        frame["perp_flow"] = 0.5
        frame["spot_ret_1m"] = 0.001
        frame["prior_4h_high"] = 104.0
        frame["prior_4h_low"] = 98.0
        frame["prior_24h_high"] = 106.0
        frame["prior_24h_low"] = 95.0

        # First ten minutes: directional, spot-confirmed, high-volume, and no
        # mature revisit of the 100 session open.
        closes = np.linspace(100.15, 101.0, 10)
        for offset, close in enumerate(closes):
            ts = index[offset]
            frame.loc[ts, "perp_open"] = 100.0 if offset == 0 else closes[offset - 1]
            frame.loc[ts, "perp_close"] = close
            frame.loc[ts, "perp_high"] = close + 0.08
            frame.loc[ts, "perp_low"] = 99.95 if offset < 2 else 100.05 + offset * 0.05
            frame.loc[ts, "perp_quote_volume"] = 120.0
            frame.loc[ts, "perp_taker_buy_quote"] = 90.0
            frame.loc[ts, "spot_open"] = 100.0 if offset == 0 else closes[offset - 1]
            frame.loc[ts, "spot_close"] = close
            frame.loc[ts, "spot_high"] = close + 0.05
            frame.loc[ts, "spot_low"] = close - 0.05

        # First counter-direction bar, then a strictly later resumption with
        # spot and taker flow aligned. Pullback extreme is the new-leg stop.
        pullback = index[10]
        frame.loc[pullback, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            101.0,
            101.05,
            100.65,
            100.75,
        ]
        resume = index[11]
        frame.loc[resume, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.75,
            101.35,
            100.70,
            101.20,
        ]
        frame.loc[resume, "perp_flow"] = 0.6
        frame.loc[resume, "spot_ret_1m"] = 0.002
        return frame, start

    def test_complete_open_drive_requires_later_pullback_and_resumption(self) -> None:
        panel, start = self._long_open_drive_panel()
        history = ThresholdHistory(
            absolute_displacements=[0.001] * 40,
            quote_volumes=[500.0] * 40,
        )
        candidate = detect_candidate(
            symbol="BTCUSDT",
            panel=panel,
            session=SessionOpen("EUROPE_0800_UTC", start),
            history=history,
        )
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.entry_ts, start + pd.Timedelta(minutes=11))
        self.assertAlmostEqual(candidate.entry, 101.20)
        self.assertAlmostEqual(candidate.stop, 100.65)
        self.assertEqual(candidate.target_source, "PRIOR_4H_HIGH")
        self.assertGreaterEqual(candidate.target_net_r, 1.0)

    def test_same_bar_stop_and_target_resolves_to_stop(self) -> None:
        panel, start = self._long_open_drive_panel()
        entry_ts = start + pd.Timedelta(minutes=11)
        candidate = CandidateTrade(
            symbol="BTCUSDT",
            session="EUROPE_0800_UTC",
            session_open_ts=start,
            entry_ts=entry_ts,
            side=1,
            entry=101.2,
            stop=100.65,
            target=104.0,
            target_source="PRIOR_4H_HIGH",
            planned_loss_rate=(101.2 - 100.65) / 101.2 + 0.002,
            target_net_r=2.0,
            drive_score=2.0,
            opening_displacement=0.01,
            opening_volume=1_000.0,
            opening_range=1.0,
            prior_4h_high=104.0,
            prior_4h_low=98.0,
            prior_24h_high=106.0,
            prior_24h_low=95.0,
        )
        first = entry_ts + pd.Timedelta(minutes=1)
        panel.loc[first, ["perp_high", "perp_low", "perp_close"]] = [104.5, 100.5, 102.0]
        scored = score_candidate(candidate, panel)
        self.assertIsNotNone(scored)
        assert scored is not None
        self.assertEqual(scored.exit_reason, "STOP")
        self.assertAlmostEqual(scored.net_r, -1.0)

    def test_global_cluster_keeps_strongest_drive_only(self) -> None:
        start = pd.Timestamp("2024-01-02T08:11:00Z")
        common = dict(
            session="EUROPE_0800_UTC",
            session_open_ts=start - pd.Timedelta(minutes=11),
            side=1,
            entry=100.0,
            stop=99.0,
            target=103.0,
            target_source="PRIOR_4H_HIGH",
            planned_loss_rate=0.012,
            target_net_r=2.0,
            opening_displacement=0.01,
            opening_volume=1_000.0,
            opening_range=1.0,
            prior_4h_high=103.0,
            prior_4h_low=98.0,
            prior_24h_high=105.0,
            prior_24h_low=95.0,
        )
        weak = CandidateTrade(
            symbol="BTCUSDT",
            entry_ts=start,
            drive_score=1.2,
            **common,
        )
        strong = CandidateTrade(
            symbol="ETHUSDT",
            entry_ts=start + pd.Timedelta(minutes=2),
            drive_score=2.0,
            **common,
        )
        later = CandidateTrade(
            symbol="SOLUSDT",
            entry_ts=start + pd.Timedelta(minutes=10),
            drive_score=1.1,
            **common,
        )
        selected = _collapse_entry_clusters([weak, strong, later])
        self.assertEqual([item.symbol for item in selected], ["ETHUSDT", "SOLUSDT"])


if __name__ == "__main__":
    unittest.main()
