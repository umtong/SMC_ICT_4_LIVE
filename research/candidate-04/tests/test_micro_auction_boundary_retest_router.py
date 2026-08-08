from __future__ import annotations

import unittest

import pandas as pd

import micro_auction_boundary_retest_router as candidate


class BoundaryRetestTests(unittest.TestCase):
    def _rich(self, *, aligned: bool = True, touched: bool = True) -> pd.DataFrame:
        rows = 270
        times = pd.date_range("2023-01-01", periods=rows, freq="min", tz="UTC")
        close = [100.0] * rows
        close[0] = 90.0
        close[250] = 100.7
        close[251] = 100.3
        close[252] = 100.2
        flow = [0.0] * rows
        ret = [0.0] * rows
        basis = [0.0] * rows
        flow[251] = -0.4
        ret[251] = -2.0
        flow[252] = 0.5 if aligned else -0.5
        ret[252] = 2.0 if aligned else -2.0
        basis[252] = 0.4 if aligned else -0.4
        low = [99.5] * rows
        high = [101.0] * rows
        low[252] = 100.0 if touched else 100.4
        return pd.DataFrame(
            {
                "open_time": times,
                "observed_time": times + pd.Timedelta(minutes=1),
                "trade_close": close,
                "ret_60s_bps": ret,
                "flow_60s": flow,
                "basis_change_5m": basis,
                "mark_low": low,
                "mark_high": high,
            }
        )

    def _signal(self) -> dict:
        return {
            "scenario": candidate.TRAPPED_REVERSAL,
            "side": 1,
            "signal_index": 250,
            "signal_time": "2023-01-01T04:10:00+00:00",
            "observe_time": "2023-01-01T04:11:00+00:00",
            "observe_time_ns": 1,
            "stop_level": 98.0,
            "event_indices": [220, 249, 250],
            "details": {
                "balance_start_index": 220,
                "balance_end_index": 239,
                "balance_high": 103.0,
                "balance_low": 100.0,
                "balance_width_atr": 3.0,
                "break_index": 245,
                "break_boundary": 100.0,
                "break_side": -1,
                "micro_balance_bars": 30,
            },
        }

    def test_distinct_counterauction_and_boundary_retest_emit_signal(self) -> None:
        routed, summary = candidate.route_signals([self._signal()], self._rich())
        self.assertEqual(len(routed), 1)
        row = routed[0]
        self.assertEqual(row["scenario"], candidate.OUTPUT_SCENARIO)
        self.assertEqual(row["signal_index"], 252)
        self.assertEqual(row["details"]["causal_target_reference"], 103.0)
        self.assertEqual(row["details"]["causal_target_observed_index"], 239)
        self.assertEqual(summary["counts"]["routed"], 1)

    def test_touch_without_aligned_confirmation_is_rejected(self) -> None:
        routed, summary = candidate.route_signals(
            [self._signal()], self._rich(aligned=False)
        )
        self.assertEqual(routed, [])
        self.assertGreater(summary["counts"]["retest_without_aligned_confirmation"], 0)

    def test_no_exact_boundary_touch_is_not_a_retest(self) -> None:
        routed, summary = candidate.route_signals(
            [self._signal()], self._rich(touched=False)
        )
        self.assertEqual(routed, [])
        self.assertEqual(summary["counts"]["no_completed_boundary_retest"], 1)

    def test_current_confirmation_uses_rich_observed_time(self) -> None:
        routed, _ = candidate.route_signals([self._signal()], self._rich())
        self.assertEqual(routed[0]["observe_time"], "2023-01-01T04:13:00+00:00")


if __name__ == "__main__":
    unittest.main()
