from __future__ import annotations

import math
import unittest

from router import BarObservation, RouteConfig, route_universe


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
MINUTE = 60_000_000_000


def base_series(*, count: int = 130, price: float = 100.0) -> list[BarObservation]:
    result: list[BarObservation] = []
    last = price
    for index in range(count):
        drift = 0.015 if index % 2 == 0 else -0.012
        open_ = last
        close = open_ + drift
        result.append(
            BarObservation(
                ts_event=(index + 1) * MINUTE,
                open=open_,
                high=max(open_, close) + 0.08,
                low=min(open_, close) - 0.08,
                close=close,
                volume=100.0 + index % 7,
            )
        )
        last = close
    return result


def append_bar(series: list[BarObservation], *, move: float, span: float, volume: float) -> None:
    previous = series[-1].close
    close = previous + move
    series.append(
        BarObservation(
            ts_event=series[-1].ts_event + MINUTE,
            open=previous,
            high=max(previous, close) + span,
            low=min(previous, close) - span,
            close=close,
            volume=volume,
        )
    )


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = RouteConfig(
            min_impulse_atr_continuation=1.20,
            min_impulse_atr_reversal=1.55,
            min_participation_ratio=1.40,
            min_response_atr=0.08,
            min_route_score=2.0,
            ambiguity_score_gap=0.05,
        )

    def test_synchronous_shock_routes_accepting_laggard(self) -> None:
        histories = {symbol: base_series() for symbol in SYMBOLS}
        for symbol, move in zip(SYMBOLS, (0.80, 0.72, 0.66, 0.18)):
            append_bar(histories[symbol], move=move, span=0.10, volume=360.0)
        for symbol, move in zip(SYMBOLS, (0.04, 0.03, 0.03, 0.24)):
            append_bar(histories[symbol], move=move, span=0.04, volume=150.0)
        winner, decisions = route_universe(
            bars_by_symbol=histories,
            config=self.config,
        )
        self.assertIsNotNone(winner)
        assert winner is not None
        self.assertEqual(winner.state, "SYNC_PROPAGATION")
        self.assertEqual(winner.symbol, "XRPUSDT")
        self.assertEqual(winner.side, 1)
        self.assertTrue(winner.stop_reference < winner.entry_reference < winner.objective_reference)
        self.assertTrue(decisions["XRPUSDT"].actionable)

    def test_isolated_ramp_failure_routes_reversal(self) -> None:
        histories = {symbol: base_series() for symbol in SYMBOLS}
        for _, move, volume in (
            (0, 0.10, 125.0),
            (1, 0.18, 150.0),
            (2, 0.28, 190.0),
            (3, 0.42, 240.0),
        ):
            for symbol in SYMBOLS:
                append_bar(
                    histories[symbol],
                    move=move if symbol == "SOLUSDT" else 0.01,
                    span=0.05 if symbol == "SOLUSDT" else 0.02,
                    volume=volume if symbol == "SOLUSDT" else 102.0,
                )
        append_bar(histories["SOLUSDT"], move=0.95, span=0.16, volume=420.0)
        for symbol in ("BTCUSDT", "ETHUSDT", "XRPUSDT"):
            append_bar(histories[symbol], move=0.01, span=0.02, volume=103.0)
        append_bar(histories["SOLUSDT"], move=-0.30, span=0.08, volume=260.0)
        for symbol in ("BTCUSDT", "ETHUSDT", "XRPUSDT"):
            append_bar(histories[symbol], move=-0.01, span=0.02, volume=104.0)

        winner, _ = route_universe(bars_by_symbol=histories, config=self.config)
        self.assertIsNotNone(winner)
        assert winner is not None
        self.assertEqual(winner.state, "ENDOGENOUS_EXHAUSTION")
        self.assertEqual(winner.symbol, "SOLUSDT")
        self.assertEqual(winner.side, -1)
        self.assertTrue(winner.objective_reference < winner.entry_reference < winner.stop_reference)
        self.assertTrue(math.isclose(winner.expected_target_r, self.config.reversal_target_r))

    def test_unaligned_clock_is_rejected(self) -> None:
        histories = {symbol: base_series() for symbol in SYMBOLS}
        last = histories["XRPUSDT"][-1]
        histories["XRPUSDT"][-1] = BarObservation(
            ts_event=last.ts_event + 1,
            open=last.open,
            high=last.high,
            low=last.low,
            close=last.close,
            volume=last.volume,
        )
        with self.assertRaises(ValueError):
            route_universe(bars_by_symbol=histories, config=self.config)

    def test_calm_market_stays_unresolved(self) -> None:
        histories = {symbol: base_series() for symbol in SYMBOLS}
        winner, decisions = route_universe(bars_by_symbol=histories, config=self.config)
        self.assertIsNone(winner)
        self.assertTrue(all(not item.actionable for item in decisions.values()))


if __name__ == "__main__":
    unittest.main()
