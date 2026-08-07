from __future__ import annotations

import unittest

import pandas as pd

from prominence_state_router import NORMAL_FAILED_AUCTION
from prominence_state_router import TRAPPED_COUNTERTREND
from prominence_state_router import route_signal


class ProminenceStateRouterTests(unittest.TestCase):
    @staticmethod
    def rich(*, ret_30s: float = 1.0, flow_30s: float = 0.2) -> pd.DataFrame:
        index = pd.DatetimeIndex([pd.Timestamp("2025-01-01T00:00:00Z")])
        return pd.DataFrame(
            {"ret_30s_bps": [ret_30s], "flow_30s": [flow_30s]},
            index=index,
        )

    @staticmethod
    def signal(scenario: str, side: int, details: dict) -> dict:
        return {
            "scenario": scenario,
            "side": side,
            "signal_index": 10,
            "signal_time": "2025-01-01T00:00:00+00:00",
            "observe_time": "2025-01-01T00:00:59.999000+00:00",
            "observe_time_ns": 1735689659999000000,
            "stop_level": 99.0,
            "details": details,
        }

    def test_internal_pool_is_routed_and_declares_fill_boundary(self) -> None:
        signal = self.signal(
            NORMAL_FAILED_AUCTION,
            1,
            {"prominence_atr": 0.75, "structure": 100.0},
        )
        routed, reason = route_signal(signal, self.rich())
        self.assertEqual(reason, "internal_pool_failed_auction")
        self.assertIsNotNone(routed)
        details = routed["details"]
        self.assertEqual(details["actual_fill_state_boundary"], 100.0)
        self.assertEqual(
            details["actual_fill_state_contract"], "pre_sweep_structure_break"
        )

    def test_one_atr_pool_is_not_the_same_immediate_reversal_state(self) -> None:
        signal = self.signal(
            NORMAL_FAILED_AUCTION,
            1,
            {"prominence_atr": 1.0, "structure": 100.0},
        )
        routed, reason = route_signal(signal, self.rich())
        self.assertIsNone(routed)
        self.assertEqual(reason, "regime_scale_pool_not_immediate_failed_auction")

    def test_trapped_inventory_requires_fresh_price_and_flow_alignment(self) -> None:
        signal = self.signal(
            TRAPPED_COUNTERTREND,
            -1,
            {"broken_pool_level": 100.0},
        )
        rejected, _ = route_signal(
            signal,
            self.rich(ret_30s=-1.0, flow_30s=0.2),
        )
        self.assertIsNone(rejected)
        accepted, reason = route_signal(
            signal,
            self.rich(ret_30s=-1.0, flow_30s=-0.2),
        )
        self.assertIsNotNone(accepted)
        self.assertEqual(
            reason, "accepted_break_inventory_trapped_with_fresh_resumption"
        )

    def test_failed_parent_continuation_family_is_not_reintroduced(self) -> None:
        signal = self.signal(
            "NON_CLIMACTIC_PARENT_AUCTION_RESUMPTION",
            1,
            {},
        )
        routed, reason = route_signal(signal, self.rich())
        self.assertIsNone(routed)
        self.assertEqual(reason, "discarded_nonportable_continuation_family")


if __name__ == "__main__":
    unittest.main()
