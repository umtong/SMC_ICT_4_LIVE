from __future__ import annotations

import unittest

import pandas as pd

from v38_failure_path_cascade import analyze_case


class V38FailurePathCascadeTest(unittest.TestCase):
    @staticmethod
    def config() -> dict[str, float | int]:
        return {
            "acceptance_close_atr": 0.05,
            "acceptance_flow_min": 0.10,
            "acceptance_efficiency_min": 0.45,
            "sweep_min_notional_burst": 1.05,
            "acceptance_depth_withdrawal_min": 0.01,
            "acceptance_close_location": 0.62,
            "acceptance_retrace_bars": 8,
            "acceptance_max_counterflow": 0.08,
            "max_hold_bars": 180,
        }

    @staticmethod
    def case() -> dict[str, object]:
        return {
            "week": "synthetic",
            "scenario_id": "smt-test",
            "symbol": "BTCUSDT",
            "side": 1,
            "swept_kind": "LOW",
            "confirmation_ts": 60_000_000_000,
            "position_close_ts": 180_000_000_000,
            "sweep_extreme": 100.0,
            "sweep_atr": 1.0,
            "realized_pnl": -1.0,
        }

    @staticmethod
    def row(
        minute: int,
        *,
        open_price: float,
        high: float,
        low: float,
        close: float,
        flow_15s: float,
        flow_60s: float,
        efficiency: float,
        burst: float,
        bid_change: float,
        ask_change: float,
        depth: float,
    ) -> dict[str, float | int]:
        return {
            "observed_time_ns": minute * 60_000_000_000,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1.0,
            "flow_15s": flow_15s,
            "flow_60s": flow_60s,
            "efficiency_60s": efficiency,
            "notional_burst": burst,
            "bid_depth_change_1_1m": bid_change,
            "ask_depth_change_1_1m": ask_change,
            "depth_imbalance_1": depth,
        }

    def test_price_reacceptance_is_reported_even_when_efficiency_is_missing(self) -> None:
        frame = pd.DataFrame(
            [
                self.row(
                    2,
                    open_price=99.8,
                    high=100.0,
                    low=98.7,
                    close=99.0,
                    flow_15s=-0.2,
                    flow_60s=-0.2,
                    efficiency=0.20,
                    burst=1.10,
                    bid_change=-0.02,
                    ask_change=0.01,
                    depth=-0.12,
                ),
                self.row(
                    3,
                    open_price=99.0,
                    high=100.1,
                    low=98.9,
                    close=99.5,
                    flow_15s=0.05,
                    flow_60s=-0.1,
                    efficiency=0.20,
                    burst=1.0,
                    bid_change=0.0,
                    ask_change=0.0,
                    depth=-0.12,
                ),
                self.row(
                    4,
                    open_price=99.5,
                    high=99.6,
                    low=97.0,
                    close=97.5,
                    flow_15s=-0.2,
                    flow_60s=-0.2,
                    efficiency=0.60,
                    burst=1.2,
                    bid_change=-0.02,
                    ask_change=0.0,
                    depth=-0.2,
                ),
            ],
        )
        result = analyze_case(self.case(), frame, self.config())
        cascade = result["component_cascade"]
        first = cascade["first_price_reacceptance"]
        self.assertIsNotNone(first)
        self.assertTrue(first["components"]["price_reaccepted"])
        self.assertFalse(first["components"]["efficient"])
        self.assertFalse(first["strict_reacceptance"])
        self.assertEqual(cascade["cumulative_bar_counts"]["price_reaccepted"], 3)
        self.assertEqual(cascade["cumulative_bar_counts"]["efficient"], 1)

    def test_first_price_reacceptance_retest_is_not_replaced_after_failure(self) -> None:
        frame = pd.DataFrame(
            [
                self.row(
                    2,
                    open_price=99.8,
                    high=100.0,
                    low=98.7,
                    close=99.0,
                    flow_15s=-0.2,
                    flow_60s=-0.2,
                    efficiency=0.50,
                    burst=1.10,
                    bid_change=-0.02,
                    ask_change=0.01,
                    depth=-0.12,
                ),
                self.row(
                    3,
                    open_price=99.0,
                    high=100.1,
                    low=98.9,
                    close=100.05,
                    flow_15s=0.2,
                    flow_60s=-0.1,
                    efficiency=0.2,
                    burst=1.0,
                    bid_change=0.0,
                    ask_change=0.0,
                    depth=0.12,
                ),
                self.row(
                    4,
                    open_price=99.5,
                    high=100.1,
                    low=98.8,
                    close=99.4,
                    flow_15s=0.05,
                    flow_60s=-0.1,
                    efficiency=0.2,
                    burst=1.0,
                    bid_change=0.0,
                    ask_change=0.0,
                    depth=-0.12,
                ),
            ],
        )
        result = analyze_case(self.case(), frame, self.config())
        retest = result["component_cascade"]["first_price_reacceptance_retest"]
        self.assertEqual(retest["result"], "FIRST_PRICE_REACCEPTANCE_RETEST_FAILED")
        self.assertFalse(retest["close_defended"])
        self.assertIsNone(result["component_cascade"]["structural_retest_excursions"])


if __name__ == "__main__":
    unittest.main()
