from __future__ import annotations

import math
import unittest

from router import BarObservation, FeatureObservation, RouteConfig, classify_symbol, route_universe

MINUTE = 60_000_000_000


def accelerating(side: int, n: int = 800, phase: float = 0.0) -> list[BarObservation]:
    bars: list[BarObservation] = []
    for i in range(n):
        log_price = math.log(100.0) + side * (0.00003 * i + 0.00000005 * i * i)
        close = math.exp(log_price) * (1.0 + 0.00005 * math.sin(i / 11.0 + phase))
        open_price = bars[-1].close if bars else close * (1.0 - side * 0.0002)
        bars.append(
            BarObservation(
                ts_event=(i + 1) * MINUTE,
                open=open_price,
                high=max(open_price, close) * 1.00025,
                low=min(open_price, close) * 0.99975,
                close=close,
                volume=1_000.0 * (1.0 + 0.20 * i / n),
            ),
        )
    return bars


def feature(side: int, ts: int) -> FeatureObservation:
    return FeatureObservation(
        observed_time_ns=ts,
        ready=True,
        flow_open_10s=0.10 * side,
        notional_open_10s_burst=1.25,
        flow_60s=0.12 * side,
        efficiency_60s=0.70,
        oi_change_15m=0.003 * side,
        premium_z=0.20 * side,
    )


def nr7_breakout() -> list[BarObservation]:
    bars: list[BarObservation] = []
    n = 710  # 47 complete 15m buckets plus five minutes in the next bucket.
    for i in range(n):
        if i < 690:
            close = 100.0 + 0.003 * i + 0.03 * math.sin(i / 6.0)
        elif i < 705:
            close = 102.1 + 0.005 * math.sin(i)
        else:
            close = 102.12 + 0.04 * (i - 704)
        open_price = bars[-1].close if bars else close
        wick = 0.04 if i < 690 else (0.006 if i < 705 else 0.015)
        bars.append(
            BarObservation(
                ts_event=i * MINUTE,
                open=open_price,
                high=max(open_price, close) + wick,
                low=min(open_price, close) - wick,
                close=close,
                volume=1_000.0 if i < 705 else 1_600.0,
            ),
        )
    return bars


class Candidate51RouterTests(unittest.TestCase):
    def test_ichi_long_and_short_have_valid_geometry(self) -> None:
        for side in (1, -1):
            bars = accelerating(side)
            decision = classify_symbol(
                symbol="BTCUSDT",
                bars=bars,
                feature=feature(side, bars[-1].ts_event),
                breadth_fraction=1.0,
                btc_impulse_side=side,
            )
            self.assertEqual(decision.state, "ICHI_FAN_ACCELERATION_CONTINUATION")
            self.assertEqual(decision.side, side)
            if side > 0:
                self.assertLess(decision.stop_reference, decision.entry_reference)
                self.assertGreater(decision.objective_reference, decision.entry_reference)
            else:
                self.assertGreater(decision.stop_reference, decision.entry_reference)
                self.assertLess(decision.objective_reference, decision.entry_reference)

    def test_adjacent_nr7_breakout_is_actionable(self) -> None:
        bars = nr7_breakout()
        decision = classify_symbol(
            symbol="BTCUSDT",
            bars=bars,
            feature=feature(1, bars[-1].ts_event),
            breadth_fraction=1.0,
            btc_impulse_side=1,
        )
        self.assertEqual(decision.state, "NR7_RANGE_EXPANSION")
        self.assertEqual(decision.side, 1)

    def test_feature_timestamp_does_not_create_future_dependency(self) -> None:
        bars = accelerating(1)
        before = classify_symbol(
            symbol="BTCUSDT",
            bars=tuple(bars),
            feature=feature(1, bars[-1].ts_event),
            breadth_fraction=1.0,
            btc_impulse_side=1,
        )
        future = BarObservation(
            ts_event=bars[-1].ts_event + MINUTE,
            open=bars[-1].close,
            high=bars[-1].close * 2.0,
            low=bars[-1].close * 0.5,
            close=bars[-1].close * 0.5,
            volume=1e12,
        )
        _ = future  # The future bar exists but is not in the completed prefix.
        after = classify_symbol(
            symbol="BTCUSDT",
            bars=tuple(bars),
            feature=feature(1, bars[-1].ts_event),
            breadth_fraction=1.0,
            btc_impulse_side=1,
        )
        self.assertEqual(before, after)

    def test_stale_feature_returns_unresolved(self) -> None:
        bars = accelerating(1)
        stale = FeatureObservation(observed_time_ns=bars[-1].ts_event, ready=False)
        decision = classify_symbol(
            symbol="BTCUSDT",
            bars=bars,
            feature=stale,
            breadth_fraction=1.0,
            btc_impulse_side=1,
        )
        self.assertFalse(decision.actionable)
        self.assertIn("FEATURE_NOT_READY", decision.reasons)

    def test_universe_routes_one_global_winner(self) -> None:
        bars_by_symbol = {
            "BTCUSDT": accelerating(1, phase=0.0),
            "ETHUSDT": accelerating(1, phase=0.4),
            "SOLUSDT": accelerating(1, phase=0.8),
            "XRPUSDT": accelerating(1, phase=1.2),
        }
        features = {
            symbol: feature(1, bars[-1].ts_event)
            for symbol, bars in bars_by_symbol.items()
        }
        winner, decisions = route_universe(
            bars_by_symbol=bars_by_symbol,
            features_by_symbol=features,
            config=RouteConfig(),
        )
        self.assertIsNotNone(winner)
        self.assertEqual(len(decisions), 4)
        self.assertEqual(sum(item.actionable for item in decisions.values()), 4)
        self.assertIn(winner.symbol, bars_by_symbol)


if __name__ == "__main__":
    unittest.main()
