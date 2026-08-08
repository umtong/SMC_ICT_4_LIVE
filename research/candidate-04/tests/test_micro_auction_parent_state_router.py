from __future__ import annotations

import unittest

import pandas as pd

import micro_auction_parent_state_router as candidate


class ParentDirectionTests(unittest.TestCase):
    def _rich(self, current: float, *, rows: int = 260) -> pd.DataFrame:
        open_time = pd.date_range("2023-01-01", periods=rows, freq="min", tz="UTC")
        close = [100.0] * rows
        close[-20:] = [current] * 20
        return pd.DataFrame(
            {
                "open_time": open_time,
                "observed_time": open_time + pd.Timedelta(minutes=1),
                "trade_close": close,
                "ret_60s_bps": [0.0] * rows,
                "flow_60s": [0.0] * rows,
                "basis_change_5m": [0.0] * rows,
                "mark_low": [99.0] * rows,
                "mark_high": [101.0] * rows,
            }
        )

    def test_parent_direction_is_side_conditioned(self) -> None:
        rich = self._rich(110.0)
        self.assertGreater(candidate.parent_directional_bps(rich, 250, 1), 0.0)
        self.assertLess(candidate.parent_directional_bps(rich, 250, -1), 0.0)


class TrappedInventoryTests(unittest.TestCase):
    def _signal(self, side: int) -> dict:
        return {
            "scenario": candidate.TRAPPED_REVERSAL,
            "side": side,
            "signal_index": 250,
            "signal_time": "2023-01-01T04:10:00+00:00",
            "observe_time": "2023-01-01T04:11:00+00:00",
            "observe_time_ns": 1,
            "stop_level": 98.0 if side > 0 else 102.0,
            "event_indices": [220, 249, 250],
            "details": {
                "balance_start_index": 220,
                "balance_end_index": 249,
                "balance_high": 101.0,
                "balance_low": 99.0,
            },
        }

    def test_parent_aligned_trapped_reversal_uses_opposite_balance_boundary(self) -> None:
        rich = ParentDirectionTests()._rich(110.0)
        routed, summary = candidate.route_signals([self._signal(1)], rich)
        self.assertEqual(len(routed), 1)
        details = routed[0]["details"]
        self.assertEqual(details["causal_target_reference"], 101.0)
        self.assertEqual(details["causal_target_observed_index"], 249)
        self.assertTrue(
            details["causal_target_source"].startswith(
                "completed_frozen_balance_"
            )
        )
        self.assertEqual(summary["counts"]["parent_aligned_trapped_reversal"], 1)

    def test_parent_misaligned_trapped_reversal_is_rejected(self) -> None:
        rich = ParentDirectionTests()._rich(90.0)
        routed, summary = candidate.route_signals([self._signal(1)], rich)
        self.assertEqual(routed, [])
        self.assertEqual(summary["counts"]["trapped_reversal_parent_misaligned"], 1)


class LiquidationFailureTests(unittest.TestCase):
    def _rich(self, recross_return: float) -> pd.DataFrame:
        rows = 270
        open_time = pd.date_range("2023-01-01", periods=rows, freq="min", tz="UTC")
        close = [100.0] * rows
        close[240] = 108.0
        close[241] = 99.5
        close[242] = 100.0
        close[243] = 101.5
        ret = [0.0] * rows
        ret[240] = 5.0
        ret[243] = recross_return
        flow = [0.0] * rows
        basis = [0.0] * rows
        flow[243] = 0.5
        basis[243] = 0.2
        return pd.DataFrame(
            {
                "open_time": open_time,
                "observed_time": open_time + pd.Timedelta(minutes=1),
                "trade_close": close,
                "ret_60s_bps": ret,
                "flow_60s": flow,
                "basis_change_5m": basis,
                "mark_low": [98.0] * rows,
                "mark_high": [109.0] * rows,
            }
        )

    def _signal(self) -> dict:
        return {
            "scenario": candidate.LIQUIDATION_REVERSAL,
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
                "break_index": 240,
                "break_side": 1,
                "break_boundary": 101.0,
                "outcome_index": 241,
            },
        }

    def test_non_climactic_reentry_failure_routes_parent_continuation(self) -> None:
        routed, summary = candidate.route_signals([self._signal()], self._rich(4.0))
        self.assertEqual(len(routed), 1)
        self.assertEqual(routed[0]["scenario"], candidate.LIQUIDATION_REENTRY_FAILURE)
        self.assertEqual(routed[0]["side"], 1)
        self.assertEqual(
            summary["counts"]["non_climactic_liquidation_reentry_failure"], 1
        )

    def test_more_climactic_second_impulse_is_not_parent_resumption(self) -> None:
        routed, summary = candidate.route_signals([self._signal()], self._rich(6.0))
        self.assertEqual(routed, [])
        self.assertEqual(
            summary["counts"]["liquidation_reentry_failure_not_confirmed"], 1
        )


if __name__ == "__main__":
    unittest.main()
