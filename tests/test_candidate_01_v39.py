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

from cross_asset_laggard_v39 import (  # noqa: E402
    add_causal_features,
    generate_symbol_plans,
)


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
) -> dict[str, float]:
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


def frame(
    value: dict[str, float],
    *,
    minute: int = 1_700_000_000_000_000_000,
) -> pd.DataFrame:
    return pd.DataFrame([value], index=[minute])


class CandidateV39Test(unittest.TestCase):
    def setUp(self) -> None:
        self.minute = 1_700_000_000_000_000_000
        self.base = {
            "BTCUSDT": frame(
                row(
                    close=101.0,
                    high=101.2,
                    low=99.8,
                    external_high=100.0,
                    external_low=95.0,
                    internal_high=99.5,
                    internal_low=98.5,
                    return_bps=40.0,
                    flow=0.25,
                ),
                minute=self.minute,
            ),
            "ETHUSDT": frame(
                row(
                    close=201.0,
                    high=201.4,
                    low=198.8,
                    external_high=200.0,
                    external_low=190.0,
                    internal_high=199.0,
                    internal_low=197.0,
                    return_bps=35.0,
                    flow=0.20,
                ),
                minute=self.minute,
            ),
            "SOLUSDT": frame(
                row(
                    close=49.5,
                    high=50.0,
                    low=49.25,
                    external_high=51.0,
                    external_low=45.0,
                    internal_high=49.0,
                    internal_low=49.2,
                    return_bps=25.0,
                    flow=0.15,
                ),
                minute=self.minute,
            ),
            "XRPUSDT": frame(
                row(
                    close=0.50,
                    high=0.505,
                    low=0.495,
                    external_high=0.55,
                    external_low=0.45,
                    internal_high=0.51,
                    internal_low=0.49,
                    return_bps=0.0,
                    flow=0.0,
                    close_location=0.5,
                ),
                minute=self.minute,
            ),
        }

    def generate(self, *, variant: str, frames=None):
        return generate_symbol_plans(
            self.base if frames is None else frames,
            variant=variant,
            evaluation_start_ns=self.minute,
            evaluation_end_ns=self.minute + 60_000_000_000,
            cost_fraction_per_side=0.0007,
            minimum_price_risk_fraction=0.65,
            minimum_net_reward_risk=1.35,
        )

    def test_two_peer_consensus_emits_primary_laggard(self) -> None:
        plans, diagnostics, counts = self.generate(variant="primary")
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].symbol, "SOLUSDT")
        self.assertEqual(
            set(diagnostics[0].leader_symbols.split("|")),
            {"BTCUSDT", "ETHUSDT"},
        )
        self.assertEqual(counts["plans_emitted"], 1)

    def test_one_peer_control_does_not_make_primary(self) -> None:
        frames = dict(self.base)
        neutral_eth = self.base["ETHUSDT"].copy()
        neutral_eth.loc[self.minute, "close"] = 199.5
        neutral_eth.loc[self.minute, "high"] = 199.8
        frames["ETHUSDT"] = neutral_eth
        primary, _, _ = self.generate(variant="primary", frames=frames)
        control, _, _ = self.generate(variant="control", frames=frames)
        self.assertEqual(primary, [])
        self.assertEqual(len(control), 1)
        self.assertEqual(control[0].symbol, "SOLUSDT")

    def test_consumed_laggard_target_is_rejected(self) -> None:
        frames = dict(self.base)
        consumed = self.base["SOLUSDT"].copy()
        consumed.loc[self.minute, "high"] = 51.1
        frames["SOLUSDT"] = consumed
        plans, _, _ = self.generate(variant="primary", frames=frames)
        self.assertEqual(plans, [])

    def test_external_liquidity_uses_only_prior_completed_minutes(self) -> None:
        minutes = [
            self.minute + index * 60_000_000_000
            for index in range(62)
        ]
        raw = pd.DataFrame(
            {
                "open": [100.0] * 62,
                "high": [100.0] * 61 + [110.0],
                "low": [99.0] * 62,
                "close": [100.0] * 62,
                "base_volume": [1.0] * 62,
                "quote_notional": [1000.0] * 62,
                "signed_quote_notional": [100.0] * 62,
                "trade_count": [1.0] * 62,
            },
            index=minutes,
        )
        featured = add_causal_features(raw)
        self.assertEqual(float(featured.iloc[-1]["external_high"]), 100.0)


if __name__ == "__main__":
    unittest.main()
