from __future__ import annotations

import sys
from pathlib import Path
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "research" / "candidate-01"
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from cross_asset_delivery_failure_v41 import generate_symbol_plans  # noqa: E402

NS = 60_000_000_000


def row(
    close, high, low, external_high, external_low, internal_high, internal_low,
    return_bps, flow, *, range_bps=20.0, median=10.0, location=0.8,
):
    return {
        "open": 0.5 * (high + low), "high": high, "low": low, "close": close,
        "base_volume": 1.0, "quote_notional": 1000.0,
        "signed_quote_notional": flow * 1000.0, "trade_count": 1.0,
        "return_bps": return_bps, "range_bps": range_bps,
        "flow_imbalance": flow, "body_efficiency": 0.8,
        "close_location": location, "external_high": external_high,
        "external_low": external_low, "internal_high": internal_high,
        "internal_low": internal_low, "range_median_bps": median,
    }


def neutral(close, high_edge, low_edge):
    return row(
        close, close + 0.1, close - 0.1, high_edge, low_edge,
        close + 0.2, close - 0.2, 0.0, 0.0,
        range_bps=5.0, location=0.5,
    )


class DeliveryFailureV41Test(unittest.TestCase):
    def setUp(self):
        self.t0 = 1_700_000_000_000_000_000
        self.t1 = self.t0 + NS
        self.btc_leader = row(101, 101.2, 99.8, 100, 95, 99.5, 98.5, 40, 0.25)
        self.eth_leader = row(201, 201.4, 198.8, 200, 190, 199, 197, 35, 0.20)

    def frames(self, sol_second):
        values = {
            "BTCUSDT": [self.btc_leader, neutral(100.5, 102, 95)],
            "ETHUSDT": [self.eth_leader, neutral(200.5, 202, 190)],
            "SOLUSDT": [neutral(49, 55, 40), sol_second],
            "XRPUSDT": [neutral(0.5, 0.55, 0.45), neutral(0.5, 0.55, 0.45)],
        }
        return {s: pd.DataFrame(v, index=[self.t0, self.t1]) for s, v in values.items()}

    def generate(self, variant, sol_second):
        return generate_symbol_plans(
            self.frames(sol_second), variant=variant,
            evaluation_start_ns=self.t1, evaluation_end_ns=self.t1 + NS,
            cost_fraction_per_side=0.0007,
            minimum_price_risk_fraction=0.65,
            minimum_net_reward_risk=1.35,
        )[0]

    def test_opposite_break_emits_only_failure_rotation(self):
        failure = row(47.5, 49, 47, 55, 40, 50, 48, -35, -0.25, location=0.2)
        primary = self.generate("primary", failure)
        control = self.generate("control", failure)
        self.assertEqual(len(primary), 1)
        self.assertEqual(primary[0].symbol, "SOLUSDT")
        self.assertEqual(primary[0].plan.side.value, "SHORT")
        self.assertEqual(control, [])

    def test_aligned_break_emits_only_assimilation_control(self):
        aligned = row(50.5, 51, 49, 55, 40, 50, 48, 30, 0.20, location=0.8)
        primary = self.generate("primary", aligned)
        control = self.generate("control", aligned)
        self.assertEqual(primary, [])
        self.assertEqual(len(control), 1)
        self.assertEqual(control[0].plan.side.value, "LONG")

    def test_consumed_opposite_target_rejects_failure(self):
        consumed = row(47.5, 49, 39.9, 55, 40, 50, 48, -35, -0.25, location=0.2)
        self.assertEqual(self.generate("primary", consumed), [])


if __name__ == "__main__":
    unittest.main()
