from __future__ import annotations

import unittest

import pandas as pd

import micro_auction_parent_state_router as base
import micro_auction_parent_state_router_v3 as candidate


class EffortContractTests(unittest.TestCase):
    def _rich(self, recross_notional: float) -> pd.DataFrame:
        rows = 270
        times = pd.date_range("2023-01-01", periods=rows, freq="min", tz="UTC")
        close = [100.0] * rows
        close[0] = 90.0
        close[1] = 90.0
        close[240] = 108.0
        close[241] = 99.5
        close[242] = 100.0
        close[243] = 101.5
        ret = [0.0] * rows
        ret[240] = 5.0
        ret[243] = 4.0
        flow = [0.0] * rows
        flow[243] = 0.5
        basis = [0.0] * rows
        basis[243] = 0.2
        notional = [100.0] * rows
        notional[243] = recross_notional
        return pd.DataFrame(
            {
                "open_time": times,
                "observed_time": times + pd.Timedelta(minutes=1),
                "trade_close": close,
                "ret_60s_bps": ret,
                "flow_60s": flow,
                "notional_60s": notional,
                "basis_change_5m": basis,
                "mark_low": [98.0] * rows,
                "mark_high": [109.0] * rows,
            }
        )

    def _signal(self, break_effort: float = 100.0) -> dict:
        return {
            "scenario": base.LIQUIDATION_REVERSAL,
            "side": -1,
            "signal_index": 241,
            "signal_time": "2023-01-01T04:01:00+00:00",
            "observe_time": "2023-01-01T04:02:00+00:00",
            "observe_time_ns": 1,
            "stop_level": 109.5,
            "event_indices": [210, 239, 240, 241],
            "details": {
                "balance_start_index": 210,
                "balance_end_index": 239,
                "balance_high": 101.0,
                "balance_low": 99.0,
                "balance_width_atr": 1.0,
                "break_effort": break_effort,
                "break_index": 240,
                "break_side": 1,
                "break_boundary": 101.0,
                "outcome_index": 241,
            },
        }

    def test_lower_effort_recross_is_kept(self) -> None:
        routed, summary = candidate.route_signals(
            [self._signal()], self._rich(100.0)
        )
        self.assertEqual(len(routed), 1)
        details = routed[0]["details"]
        self.assertAlmostEqual(
            details["liquidation_reentry_failure_effort_ratio"], 0.5
        )
        self.assertEqual(
            summary["v58c_counts"]["non_climactic_effort_continuation"], 1
        )

    def test_higher_effort_recross_is_rejected(self) -> None:
        routed, summary = candidate.route_signals(
            [self._signal()], self._rich(300.0)
        )
        self.assertEqual(routed, [])
        self.assertEqual(
            summary["v58c_counts"][
                "liquidation_recross_more_climactic_than_break"
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()
