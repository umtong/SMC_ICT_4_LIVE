from __future__ import annotations

import unittest

from market_leadership import MINUTE_NS, MarketLeadershipGate


class MarketLeadershipImpulseTests(unittest.TestCase):
    SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    NOTIONAL = {
        "BTCUSDT": 5000.0,
        "ETHUSDT": 4000.0,
        "SOLUSDT": 3000.0,
        "XRPUSDT": 2000.0,
    }

    def _observe(
        self,
        gate: MarketLeadershipGate,
        minute: int,
        closes: dict[str, float],
    ) -> None:
        gate.observe_batch(
            minute * MINUTE_NS,
            {
                symbol: (
                    closes[symbol],
                    self.NOTIONAL[symbol] / closes[symbol],
                )
                for symbol in self.SYMBOLS
            },
        )

    def _decision(self, *, strong: bool):
        gate = MarketLeadershipGate(
            self.SYMBOLS,
            lookback_bars=5,
            max_history_bars=20,
            confirmation_impulse_lookback_bars=3,
            minimum_follower_confirmation_impulse=1.0,
        )
        paths = [
            {symbol: 100.0 for symbol in self.SYMBOLS},
            {
                "BTCUSDT": 100.1,
                "ETHUSDT": 100.1,
                "SOLUSDT": 100.1,
                "XRPUSDT": 100.1,
            },
            {symbol: 100.0 for symbol in self.SYMBOLS},
            {
                "BTCUSDT": 100.1,
                "ETHUSDT": 100.1,
                "SOLUSDT": 100.1,
                "XRPUSDT": 100.1,
            },
            {symbol: 100.0 for symbol in self.SYMBOLS},
        ]
        for minute, closes in enumerate(paths, start=1):
            self._observe(gate, minute, closes)
        candidate_close = 100.5 if strong else 100.03
        self._observe(
            gate,
            6,
            {
                "BTCUSDT": 100.2,
                "ETHUSDT": 100.2,
                "SOLUSDT": candidate_close,
                "XRPUSDT": 100.2,
            },
        )
        return gate.decide(
            symbol="SOLUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=5 * MINUTE_NS,
            confirmation_ts_ns=6 * MINUTE_NS,
        )

    def test_unanimous_peers_cannot_replace_local_displacement(self) -> None:
        result = self._decision(strong=False)
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "FOLLOWER_FAR_WEAK_LOCAL_DISPLACEMENT",
        )
        self.assertIsNotNone(result.confirmation_impulse)
        self.assertLess(result.confirmation_impulse, 1.0)

    def test_strong_local_displacement_confirms_unanimous_peers(self) -> None:
        result = self._decision(strong=True)
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "FOLLOWER_FAR_UNANIMOUS_PEERS")
        self.assertIsNotNone(result.confirmation_impulse)
        self.assertGreater(result.confirmation_impulse, 1.0)


if __name__ == "__main__":
    unittest.main()
