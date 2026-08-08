from __future__ import annotations

import unittest

import pandas as pd

import micro_auction_parent_state_router as base
import micro_auction_parent_state_router_v2 as candidate


class TargetContractTests(unittest.TestCase):
    def test_balance_boundary_is_checkpoint_not_final_target(self) -> None:
        rows = 260
        times = pd.date_range("2023-01-01", periods=rows, freq="min", tz="UTC")
        close = [100.0] * rows
        close[-20:] = [110.0] * 20
        rich = pd.DataFrame(
            {
                "open_time": times,
                "observed_time": times + pd.Timedelta(minutes=1),
                "trade_close": close,
                "ret_60s_bps": [0.0] * rows,
                "flow_60s": [0.0] * rows,
                "basis_change_5m": [0.0] * rows,
                "mark_low": [99.0] * rows,
                "mark_high": [111.0] * rows,
            }
        )
        signal = {
            "scenario": base.TRAPPED_REVERSAL,
            "side": 1,
            "signal_index": 250,
            "signal_time": times[250].isoformat(),
            "observe_time": (times[250] + pd.Timedelta(minutes=1)).isoformat(),
            "observe_time_ns": int((times[250] + pd.Timedelta(minutes=1)).value),
            "stop_level": 98.0,
            "event_indices": [220, 249, 250],
            "details": {
                "balance_start_index": 220,
                "balance_end_index": 249,
                "balance_high": 101.0,
                "balance_low": 99.0,
            },
        }
        routed, summary = candidate.route_signals([signal], rich)
        self.assertEqual(len(routed), 1)
        details = routed[0]["details"]
        self.assertEqual(details["first_balance_checkpoint"], 101.0)
        self.assertNotIn("causal_target_reference", details)
        self.assertNotIn("causal_target_source", details)
        self.assertNotIn("causal_target_observed_index", details)
        self.assertEqual(summary["balance_checkpoint_signals"], 1)


if __name__ == "__main__":
    unittest.main()
