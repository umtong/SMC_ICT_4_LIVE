from __future__ import annotations

import unittest

import pandas as pd

from v12_initial_balance_extension_study import BalanceCandidate
from v12_initial_balance_extension_study import BalanceHistory
from v12_initial_balance_extension_study import collapse_global_clusters
from v12_initial_balance_extension_study import detect_candidate
from v12_initial_balance_extension_study import score_candidate


class Candidate16V12InitialBalanceTests(unittest.TestCase):
    def _panel(self) -> tuple[pd.DataFrame, pd.Timestamp]:
        start = pd.Timestamp("2024-01-02T08:00:00Z")
        index = pd.date_range(start, periods=240, freq="min", tz="UTC").as_unit("ns")
        frame = pd.DataFrame(index=index)
        frame["minute"] = index
        frame["perp_open"] = 100.0
        frame["perp_high"] = 100.4
        frame["perp_low"] = 99.6
        frame["perp_close"] = 100.0
        frame["perp_quote_volume"] = 100.0
        frame["perp_taker_buy_quote"] = 50.0
        frame["perp_flow"] = 0.0
        frame["spot_open"] = 100.0
        frame["spot_high"] = 100.3
        frame["spot_low"] = 99.7
        frame["spot_close"] = 100.0
        frame["spot_quote_volume"] = 100.0
        frame["spot_ret_1m"] = 0.0

        # Narrow, rotational 60-minute Initial Balance: [99.5, 100.5].
        frame.loc[index[:60], "perp_high"] = 100.5
        frame.loc[index[:60], "perp_low"] = 99.5
        frame.loc[index[0], "perp_open"] = 100.0
        frame.loc[index[59], "perp_close"] = 100.1

        breakout = index[60]
        frame.loc[breakout, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.2,
            100.9,
            100.15,
            100.8,
        ]
        frame.loc[breakout, "perp_quote_volume"] = 200.0
        frame.loc[breakout, "perp_flow"] = 0.6
        frame.loc[breakout, "spot_ret_1m"] = 0.002

        retest = index[61]
        frame.loc[retest, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.8,
            100.7,
            100.45,
            100.6,
        ]
        frame.loc[retest, "perp_flow"] = -0.1
        frame.loc[retest, "spot_ret_1m"] = -0.0002

        resume = index[62]
        frame.loc[resume, ["perp_open", "perp_high", "perp_low", "perp_close"]] = [
            100.6,
            100.85,
            100.55,
            100.75,
        ]
        frame.loc[resume, "perp_flow"] = 0.5
        frame.loc[resume, "spot_ret_1m"] = 0.001
        return frame, start

    def test_narrow_balance_breakout_retest_and_later_resumption(self) -> None:
        panel, start = self._panel()
        history = BalanceHistory([0.02] * 40)
        candidate = detect_candidate(
            symbol="BTCUSDT",
            panel=panel,
            session_name="EUROPE_0800_UTC",
            session_ts=start,
            history=history,
        )
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.breakout_ts, start + pd.Timedelta(minutes=60))
        self.assertEqual(candidate.retest_ts, start + pd.Timedelta(minutes=61))
        self.assertEqual(candidate.entry_ts, start + pd.Timedelta(minutes=62))
        self.assertGreater(candidate.entry_ts, candidate.retest_ts)
        self.assertEqual(candidate.target_source, "ONE_INITIAL_BALANCE_RANGE_EXTENSION")
        self.assertAlmostEqual(candidate.target, 101.5)
        self.assertGreaterEqual(candidate.target_net_r, 1.0)
        self.assertEqual(len(history.normalized_ranges), 41)

    def test_first_retest_failure_is_final(self) -> None:
        panel, start = self._panel()
        failed = start + pd.Timedelta(minutes=61)
        panel.loc[failed, "perp_close"] = 100.4
        candidate = detect_candidate(
            symbol="BTCUSDT",
            panel=panel,
            session_name="EUROPE_0800_UTC",
            session_ts=start,
            history=BalanceHistory([0.02] * 40),
        )
        self.assertIsNone(candidate)

    def test_same_bar_stop_and_projection_target_resolves_to_stop(self) -> None:
        panel, start = self._panel()
        candidate = BalanceCandidate(
            symbol="BTCUSDT",
            session="EUROPE_0800_UTC",
            session_open_ts=start,
            breakout_ts=start + pd.Timedelta(minutes=60),
            retest_ts=start + pd.Timedelta(minutes=61),
            entry_ts=start + pd.Timedelta(minutes=62),
            side=1,
            entry=100.75,
            stop=100.45,
            target=101.5,
            target_source="ONE_INITIAL_BALANCE_RANGE_EXTENSION",
            planned_loss_rate=(100.75 - 100.45) / 100.75 + 0.002,
            target_net_r=1.1,
            ib_high=100.5,
            ib_low=99.5,
            ib_range=1.0,
            ib_normalized_range=0.01,
            ib_narrow_threshold=0.02,
            ib_efficiency=0.1,
            breakout_close=100.8,
            breakout_volume_ratio=2.0,
            expansion_score=1.2,
        )
        first = candidate.entry_ts + pd.Timedelta(minutes=1)
        panel.loc[first, ["perp_high", "perp_low", "perp_close"]] = [101.6, 100.4, 101.0]
        scored = score_candidate(candidate, panel)
        self.assertIsNotNone(scored)
        assert scored is not None
        self.assertEqual(scored.exit_reason, "STOP")
        self.assertAlmostEqual(scored.net_r, -1.0)

    def test_global_cluster_selects_strongest_expansion(self) -> None:
        start = pd.Timestamp("2024-01-02T09:02:00Z")
        common = dict(
            session="EUROPE_0800_UTC",
            session_open_ts=start - pd.Timedelta(minutes=62),
            breakout_ts=start - pd.Timedelta(minutes=2),
            retest_ts=start - pd.Timedelta(minutes=1),
            side=1,
            entry=100.0,
            stop=99.5,
            target=101.0,
            target_source="ONE_INITIAL_BALANCE_RANGE_EXTENSION",
            planned_loss_rate=0.007,
            target_net_r=1.1,
            ib_high=100.0,
            ib_low=99.0,
            ib_range=1.0,
            ib_normalized_range=0.01,
            ib_narrow_threshold=0.02,
            ib_efficiency=0.1,
            breakout_close=100.2,
            breakout_volume_ratio=2.0,
        )
        weak = BalanceCandidate(symbol="BTCUSDT", entry_ts=start, expansion_score=1.0, **common)
        strong = BalanceCandidate(
            symbol="ETHUSDT",
            entry_ts=start + pd.Timedelta(minutes=2),
            expansion_score=2.0,
            **common,
        )
        later = BalanceCandidate(
            symbol="SOLUSDT",
            entry_ts=start + pd.Timedelta(minutes=10),
            expansion_score=1.5,
            **common,
        )
        selected = collapse_global_clusters([weak, strong, later])
        self.assertEqual([item.symbol for item in selected], ["ETHUSDT", "SOLUSDT"])


if __name__ == "__main__":
    unittest.main()
