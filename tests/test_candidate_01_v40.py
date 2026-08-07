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

from persistent_cross_asset_delivery_v40 import generate_symbol_plans  # noqa: E402

MINUTE_NS = 60_000_000_000


def row(
    *,
    close: float,
    high: float,
    low: float,
    external_high: float,
    external_low: float,
    internal_high: float,
    internal_low: float,
    return_bps: float,
    flow: float,
    range_bps: float = 20.0,
    range_median_bps: float = 10.0,
    close_location: float = 0.8,
):
    return {
        "open": 0.5 * (high + low),
        "high": high,
        "low": low,
        "close": close,
        "base_volume": 1.0,
        "quote_notional": 1000.0,
        "signed_quote_notional": flow * 1000.0,
        "trade_count": 1.0,
        "return_bps": return_bps,
        "range_bps": range_bps,
        "flow_imbalance": flow,
        "body_efficiency": 0.8,
        "close_location": close_location,
        "external_high": external_high,
        "external_low": external_low,
        "internal_high": internal_high,
        "internal_low": internal_low,
        "range_median_bps": range_median_bps,
    }


def neutral(*, close: float, external_high: float, external_low: float):
    return row(
        close=close,
        high=close + 0.1,
        low=close - 0.1,
        external_high=external_high,
        external_low=external_low,
        internal_high=close + 0.2,
        internal_low=close - 0.2,
        return_bps=0.0,
        flow=0.0,
        range_bps=5.0,
        close_location=0.5,
    )


class PersistentDeliveryV40Test(unittest.TestCase):
    def setUp(self) -> None:
        self.t0 = 1_700_000_000_000_000_000
        self.t1 = self.t0 + MINUTE_NS
        self.leader_btc = row(
            close=101.0, high=101.2, low=99.8,
            external_high=100.0, external_low=95.0,
            internal_high=99.5, internal_low=98.5,
            return_bps=40.0, flow=0.25,
        )
        self.leader_eth = row(
            close=201.0, high=201.4, low=198.8,
            external_high=200.0, external_low=190.0,
            internal_high=199.0, internal_low=197.0,
            return_bps=35.0, flow=0.20,
        )
        self.laggard_sol = row(
            close=49.5, high=50.0, low=49.25,
            external_high=51.0, external_low=45.0,
            internal_high=49.0, internal_low=49.2,
            return_bps=25.0, flow=0.15,
        )

    def frames(self, *, btc_second=None, eth_second=None):
        values = {
            "BTCUSDT": [
                self.leader_btc,
                btc_second or neutral(close=100.5, external_high=102.0, external_low=95.0),
            ],
            "ETHUSDT": [
                self.leader_eth,
                eth_second or neutral(close=200.5, external_high=202.0, external_low=190.0),
            ],
            "SOLUSDT": [
                neutral(close=48.8, external_high=51.0, external_low=45.0),
                self.laggard_sol,
            ],
            "XRPUSDT": [
                neutral(close=0.50, external_high=0.55, external_low=0.45),
                neutral(close=0.50, external_high=0.55, external_low=0.45),
            ],
        }
        return {
            symbol: pd.DataFrame(rows, index=[self.t0, self.t1])
            for symbol, rows in values.items()
        }

    def generate(self, *, variant: str, frames):
        return generate_symbol_plans(
            frames,
            variant=variant,
            evaluation_start_ns=self.t1,
            evaluation_end_ns=self.t1 + MINUTE_NS,
            cost_fraction_per_side=0.0007,
            minimum_price_risk_fraction=0.65,
            minimum_net_reward_risk=1.35,
        )

    def test_persistent_states_emit_after_leader_breakout_minute(self) -> None:
        frames = self.frames()
        primary, diagnostics, _ = self.generate(variant="primary", frames=frames)
        control, _, _ = self.generate(variant="control", frames=frames)
        self.assertEqual(len(primary), 1)
        self.assertEqual(primary[0].symbol, "SOLUSDT")
        self.assertEqual(set(diagnostics[0].leader_symbols.split("|")), {"BTCUSDT", "ETHUSDT"})
        self.assertEqual(control, [])

    def test_boundary_reentry_invalidates_persistent_leader(self) -> None:
        btc_reentry = neutral(close=99.5, external_high=102.0, external_low=95.0)
        primary, _, counts = self.generate(
            variant="primary",
            frames=self.frames(btc_second=btc_reentry),
        )
        self.assertEqual(primary, [])
        self.assertEqual(counts["leader_states_invalidated"], 1)

    def test_same_minute_two_leaders_match_control(self) -> None:
        primary, _, _ = self.generate(
            variant="primary",
            frames=self.frames(btc_second=self.leader_btc, eth_second=self.leader_eth),
        )
        control, _, _ = self.generate(
            variant="control",
            frames=self.frames(btc_second=self.leader_btc, eth_second=self.leader_eth),
        )
        self.assertEqual(len(primary), 1)
        self.assertEqual(len(control), 1)
        self.assertEqual(primary[0].symbol, control[0].symbol)


if __name__ == "__main__":
    unittest.main()
