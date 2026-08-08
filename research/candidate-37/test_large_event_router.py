from __future__ import annotations

from collections import OrderedDict
import unittest

import pandas as pd

from large_event_router import LargeEventConfig, SYMBOLS, route_large_event


MINUTE = 60_000_000_000


def frame(symbol: str, count: int = 140) -> pd.DataFrame:
    rows = []
    for index in range(count):
        rows.append({
            "ts": (index + 1) * MINUTE,
            "open": 100.0,
            "high": 100.05,
            "low": 99.95,
            "close": 100.0,
            "atr_bps": 10.0,
            "ret1_bps": 0.0,
            "ret5_bps": 0.0,
            "prior_high60": 100.60,
            "prior_low60": 99.40,
            "prior_range60_bps": 120.0,
            "feature_ready": True,
            "flow_15s": 0.0,
            "flow_60s": 0.0,
            "efficiency_60s": 0.4,
            "notional_burst": 1.0,
            "oi_change_15m": 0.0,
            "premium_change_15m": 0.0,
        })
    return pd.DataFrame(rows)


class LargeEventRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frames = OrderedDict((symbol, frame(symbol)) for symbol in SYMBOLS)
        self.index = 120

    def test_new_risk_breakout_routes_with_cost_scale_objective(self) -> None:
        btc = self.frames["BTCUSDT"]
        btc.loc[self.index, [
            "open", "high", "low", "close", "ret1_bps", "ret5_bps",
            "flow_15s", "flow_60s", "efficiency_60s", "notional_burst",
            "oi_change_15m", "premium_change_15m",
        ]] = [100.58, 100.76, 100.55, 100.70, 12.0, 18.0, 0.30, 0.55, 0.55, 2.5, 0.0010, 0.0001]
        self.frames["ETHUSDT"].loc[self.index, "ret5_bps"] = 8.0
        winner, candidates = route_large_event(
            frames=self.frames, index=self.index, config=LargeEventConfig(),
        )
        self.assertIsNotNone(winner)
        assert winner is not None
        self.assertEqual(winner.symbol, "BTCUSDT")
        self.assertEqual(winner.state, "RISK_BUILD_BREAKOUT")
        self.assertEqual(winner.side, 1)
        self.assertGreater(winner.objective_reference, btc.loc[self.index, "close"])
        self.assertLess(winner.stop_reference, btc.loc[self.index, "close"])
        self.assertEqual(len(candidates), 1)

    def test_calm_market_has_no_route(self) -> None:
        winner, candidates = route_large_event(frames=self.frames, index=self.index)
        self.assertIsNone(winner)
        self.assertEqual(candidates, [])

    def test_unaligned_clock_is_rejected(self) -> None:
        self.frames["XRPUSDT"].loc[self.index, "ts"] += 1
        with self.assertRaises(ValueError):
            route_large_event(frames=self.frames, index=self.index)


if __name__ == "__main__":
    unittest.main()
