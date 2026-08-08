from __future__ import annotations

import math
import unittest

from router import BarObservation, FeatureObservation, RouteConfig, route_universe


def make_bars(*, side: int, reverse: bool = False, start: float = 100.0) -> list[BarObservation]:
    rows: list[BarObservation] = []
    price = start
    ts = 0
    for index in range(80):
        drift = 0.015 * side if index >= 62 else 0.002 * (1 if index % 2 == 0 else -1)
        if index >= 77:
            drift = (-0.12 * side if reverse else 0.08 * side)
        open_price = price
        close = max(1.0, open_price + drift)
        high = max(open_price, close) + 0.025
        low = min(open_price, close) - 0.025
        if reverse and index == 77:
            high = max(high, open_price + 0.16) if side > 0 else high
            low = min(low, open_price - 0.16) if side < 0 else low
        rows.append(
            BarObservation(
                ts_event=ts,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=100.0 if index < 77 else 170.0,
            ),
        )
        price = close
        ts += 60_000_000_000
    return rows


class RouterTest(unittest.TestCase):
    def test_continuation_selects_single_best_symbol(self) -> None:
        bars = {
            "BTCUSDT": make_bars(side=1),
            "ETHUSDT": make_bars(side=1, start=50.0),
            "SOLUSDT": make_bars(side=1, start=20.0),
            "XRPUSDT": make_bars(side=-1, start=1.0),
        }
        features = {
            symbol: FeatureObservation(
                observed_time_ns=items[-3].ts_event,
                ready=True,
                flow_open_10s=0.20 if symbol != "XRPUSDT" else -0.20,
                notional_open_10s_burst=1.45,
                flow_60s=0.15 if symbol != "XRPUSDT" else -0.15,
                efficiency_60s=0.55,
                oi_change_15m=0.004,
            )
            for symbol, items in bars.items()
        }
        winner, decisions = route_universe(
            bars_by_symbol=bars,
            features_by_symbol=features,
            config=RouteConfig(min_route_score=2.5, ambiguity_score_gap=0.0),
        )
        self.assertIsNotNone(winner)
        assert winner is not None
        self.assertEqual(winner.state, "PHASE_ACCEPTED_CONTINUATION")
        self.assertEqual(winner.side, 1)
        self.assertTrue(winner.stop_reference < winner.entry_reference < winner.objective_reference)
        self.assertEqual(len(decisions), 4)

    def test_failed_boundary_acceptance_routes_reversal(self) -> None:
        bars = {symbol: make_bars(side=1, reverse=True, start=100.0 + i * 10) for i, symbol in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"))}
        features = {
            symbol: FeatureObservation(
                observed_time_ns=items[-3].ts_event,
                ready=True,
                flow_open_10s=0.35,
                notional_open_10s_burst=1.8,
                flow_60s=-0.18,
                efficiency_60s=0.18,
                oi_change_15m=-0.006,
                premium_z=2.2,
            )
            for symbol, items in bars.items()
        }
        winner, _ = route_universe(
            bars_by_symbol=bars,
            features_by_symbol=features,
            config=RouteConfig(min_route_score=2.4, ambiguity_score_gap=0.0),
        )
        self.assertIsNotNone(winner)
        assert winner is not None
        self.assertEqual(winner.state, "PHASE_EXHAUSTION_REVERSAL")
        self.assertEqual(winner.side, -1)
        self.assertTrue(winner.objective_reference < winner.entry_reference < winner.stop_reference)

    def test_unready_features_fail_closed(self) -> None:
        bars = {"BTCUSDT": make_bars(side=1)}
        winner, decisions = route_universe(
            bars_by_symbol=bars,
            features_by_symbol={"BTCUSDT": FeatureObservation(0, ready=False)},
        )
        self.assertIsNone(winner)
        self.assertEqual(decisions["BTCUSDT"].state, "UNRESOLVED")

    def test_no_future_feature_dependency(self) -> None:
        bars = {"BTCUSDT": make_bars(side=1)}
        feature = FeatureObservation(
            observed_time_ns=bars["BTCUSDT"][-3].ts_event,
            ready=True,
            flow_open_10s=0.2,
            notional_open_10s_burst=1.4,
            flow_60s=0.2,
            efficiency_60s=0.6,
        )
        winner_a, _ = route_universe(
            bars_by_symbol=bars,
            features_by_symbol={"BTCUSDT": feature},
            config=RouteConfig(min_route_score=2.0),
        )
        future_only = FeatureObservation(
            observed_time_ns=feature.observed_time_ns + 60_000_000_000,
            ready=True,
            flow_open_10s=-0.99,
            notional_open_10s_burst=99.0,
            flow_60s=-0.99,
            efficiency_60s=0.0,
        )
        self.assertTrue(math.isfinite(float(feature.flow_open_10s)))
        self.assertNotEqual(feature, future_only)
        self.assertIsNotNone(winner_a)


if __name__ == "__main__":
    unittest.main()
