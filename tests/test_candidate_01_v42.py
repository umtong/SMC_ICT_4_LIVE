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

from pairwise_rotation_v42 import generate_symbol_plans  # noqa: E402

NS = 60_000_000_000


def row(close, high, low, eh, el, ih, il, ret, flow, *, rng=20, med=10, loc=0.8):
    return {
        "open": (high + low) / 2, "high": high, "low": low, "close": close,
        "base_volume": 1.0, "quote_notional": 1000.0,
        "signed_quote_notional": flow * 1000.0, "trade_count": 1.0,
        "return_bps": ret, "range_bps": rng, "flow_imbalance": flow,
        "body_efficiency": 0.8, "close_location": loc,
        "external_high": eh, "external_low": el,
        "internal_high": ih, "internal_low": il,
        "range_median_bps": med,
    }


def neutral(close, eh, el):
    return row(close, close + .01, close - .01, eh, el, close + .02, close - .02, 0, 0, rng=5, loc=.5)


class PairwiseRotationV42Test(unittest.TestCase):
    def setUp(self):
        self.t0 = 1_700_000_000_000_000_000
        self.t1 = self.t0 + NS
        self.btc = row(101, 101.2, 99.8, 100, 95, 99.5, 98.5, 40, .25)
        self.eth = row(201, 201.4, 198.8, 200, 190, 199, 197, 35, .20)
        self.sol = row(51, 51.2, 49.8, 50, 45, 49.5, 48.5, 40, .25)
        self.failure = row(.47, .50, .46, .60, .40, .52, .48, -35, -.25, loc=.2)

    def frames(self, three_peers=False):
        values = {
            "BTCUSDT": [self.btc, neutral(100.5, 102, 95)],
            "ETHUSDT": [self.eth, neutral(200.5, 202, 190)],
            "SOLUSDT": [self.sol if three_peers else neutral(49, 55, 40), neutral(50.5, 55, 40)],
            "XRPUSDT": [neutral(.5, .60, .40), self.failure],
        }
        return {s: pd.DataFrame(v, index=[self.t0, self.t1]) for s, v in values.items()}

    def generate(self, variant, three_peers=False):
        return generate_symbol_plans(
            self.frames(three_peers), variant=variant,
            evaluation_start_ns=self.t1, evaluation_end_ns=self.t1 + NS,
            cost_fraction_per_side=.0007,
            minimum_price_risk_fraction=.65,
            minimum_net_reward_risk=1.35,
        )[0]

    def test_exact_two_peer_failure_is_retained(self):
        self.assertEqual(len(self.generate("primary")), 1)
        self.assertEqual(len(self.generate("control")), 1)

    def test_three_peer_systemic_breadth_is_excluded_only_from_primary(self):
        self.assertEqual(self.generate("primary", True), [])
        control = self.generate("control", True)
        self.assertEqual(len(control), 1)
        self.assertEqual(control[0].symbol, "XRPUSDT")
        self.assertEqual(control[0].plan.side.value, "SHORT")


if __name__ == "__main__":
    unittest.main()
