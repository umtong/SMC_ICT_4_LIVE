from __future__ import annotations

import unittest

from market_leadership import MINUTE_NS, MarketLeadershipGate


class MarketLeadershipGateTests(unittest.TestCase):
    SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")

    @staticmethod
    def _batch(
        gate: MarketLeadershipGate,
        minute: int,
        closes: dict[str, float],
        notionals: dict[str, float],
    ) -> int:
        ts_ns = minute * MINUTE_NS
        gate.observe_batch(
            ts_ns,
            {
                symbol: (closes[symbol], notionals[symbol] / closes[symbol])
                for symbol in gate.symbols
            },
        )
        return ts_ns

    def _ready_gate(self) -> tuple[MarketLeadershipGate, int]:
        gate = MarketLeadershipGate(self.SYMBOLS, lookback_bars=3, max_history_bars=12)
        notionals = {
            "BTCUSDT": 1000.0,
            "ETHUSDT": 5000.0,
            "SOLUSDT": 900.0,
            "XRPUSDT": 800.0,
        }
        for minute in range(1, 4):
            self._batch(
                gate,
                minute,
                {symbol: 100.0 for symbol in self.SYMBOLS},
                notionals,
            )
        return gate, 3 * MINUTE_NS

    def test_leader_is_dynamic_not_hard_coded_to_btc(self) -> None:
        gate, sweep_ts = self._ready_gate()
        confirmation_ts = self._batch(
            gate,
            4,
            {symbol: 101.0 for symbol in self.SYMBOLS},
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        result = gate.decide(
            symbol="ETHUSDT",
            scenario="AAC",
            direction="LONG",
            sweep_ts_ns=sweep_ts,
            confirmation_ts_ns=confirmation_ts,
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.leader, "ETHUSDT")
        self.assertEqual(result.reason, "LEADER_PRICE_DISCOVERY")

    def test_follower_far_requires_every_peer_to_move_with_reversal(self) -> None:
        gate, sweep_ts = self._ready_gate()
        confirmation_ts = self._batch(
            gate,
            4,
            {
                "BTCUSDT": 101.0,
                "ETHUSDT": 102.0,
                "SOLUSDT": 103.0,
                "XRPUSDT": 101.0,
            },
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        result = gate.decide(
            symbol="SOLUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=sweep_ts,
            confirmation_ts_ns=confirmation_ts,
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "FOLLOWER_FAR_UNANIMOUS_PEERS")
        self.assertTrue(all(value > 0 for value in result.peer_returns.values()))

    def test_follower_far_abstains_when_one_peer_disagrees(self) -> None:
        gate, sweep_ts = self._ready_gate()
        confirmation_ts = self._batch(
            gate,
            4,
            {
                "BTCUSDT": 101.0,
                "ETHUSDT": 102.0,
                "SOLUSDT": 100.5,
                "XRPUSDT": 99.0,
            },
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        result = gate.decide(
            symbol="SOLUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=sweep_ts,
            confirmation_ts_ns=confirmation_ts,
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "FOLLOWER_FAR_PEER_DISAGREEMENT")

    def test_directional_leader_recovery_approves_mixed_peer_far(self) -> None:
        gate = MarketLeadershipGate(self.SYMBOLS, lookback_bars=3, max_history_bars=12)
        notionals = {
            "BTCUSDT": 1000.0,
            "ETHUSDT": 5000.0,
            "SOLUSDT": 900.0,
            "XRPUSDT": 800.0,
        }
        self._batch(
            gate,
            1,
            {symbol: 100.0 for symbol in self.SYMBOLS},
            notionals,
        )
        self._batch(
            gate,
            2,
            {
                "BTCUSDT": 100.5,
                "ETHUSDT": 100.0,
                "SOLUSDT": 102.0,
                "XRPUSDT": 99.5,
            },
            notionals,
        )
        sweep_ts = self._batch(
            gate,
            3,
            {
                "BTCUSDT": 101.0,
                "ETHUSDT": 100.0,
                "SOLUSDT": 104.0,
                "XRPUSDT": 99.0,
            },
            notionals,
        )
        confirmation_ts = self._batch(
            gate,
            4,
            {
                "BTCUSDT": 102.0,
                "ETHUSDT": 99.5,
                "SOLUSDT": 105.5,
                "XRPUSDT": 100.0,
            },
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        result = gate.decide(
            symbol="SOLUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=sweep_ts,
            confirmation_ts_ns=confirmation_ts,
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "FOLLOWER_FAR_DIRECTIONAL_LEADER_RECOVERY")
        self.assertGreater(
            result.directional_returns["SOLUSDT"],
            max(
                value
                for peer, value in result.directional_returns.items()
                if peer != "SOLUSDT"
            ),
        )

    def test_directional_leader_without_recovery_still_abstains(self) -> None:
        gate = MarketLeadershipGate(self.SYMBOLS, lookback_bars=3, max_history_bars=12)
        notionals = {
            "BTCUSDT": 1000.0,
            "ETHUSDT": 5000.0,
            "SOLUSDT": 900.0,
            "XRPUSDT": 800.0,
        }
        self._batch(
            gate,
            1,
            {symbol: 100.0 for symbol in self.SYMBOLS},
            notionals,
        )
        self._batch(
            gate,
            2,
            {
                "BTCUSDT": 100.5,
                "ETHUSDT": 100.0,
                "SOLUSDT": 102.0,
                "XRPUSDT": 99.5,
            },
            notionals,
        )
        sweep_ts = self._batch(
            gate,
            3,
            {
                "BTCUSDT": 101.0,
                "ETHUSDT": 100.0,
                "SOLUSDT": 104.0,
                "XRPUSDT": 99.0,
            },
            notionals,
        )
        confirmation_ts = self._batch(
            gate,
            4,
            {
                "BTCUSDT": 102.0,
                "ETHUSDT": 99.5,
                "SOLUSDT": 103.0,
                "XRPUSDT": 100.0,
            },
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        result = gate.decide(
            symbol="SOLUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=sweep_ts,
            confirmation_ts_ns=confirmation_ts,
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "FOLLOWER_FAR_PEER_DISAGREEMENT")

    def test_follower_aac_abstains_even_when_peers_move_together(self) -> None:
        gate, sweep_ts = self._ready_gate()
        confirmation_ts = self._batch(
            gate,
            4,
            {symbol: 99.0 for symbol in self.SYMBOLS},
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        result = gate.decide(
            symbol="SOLUSDT",
            scenario="AAC",
            direction="SHORT",
            sweep_ts_ns=sweep_ts,
            confirmation_ts_ns=confirmation_ts,
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "FOLLOWER_AAC_WITHOUT_LEADERSHIP")

    def test_missing_full_lookback_fails_closed(self) -> None:
        gate = MarketLeadershipGate(self.SYMBOLS, lookback_bars=3, max_history_bars=8)
        self._batch(
            gate,
            1,
            {symbol: 100.0 for symbol in self.SYMBOLS},
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        sweep_ts = 1 * MINUTE_NS
        confirmation_ts = self._batch(
            gate,
            2,
            {symbol: 101.0 for symbol in self.SYMBOLS},
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        result = gate.decide(
            symbol="BTCUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=sweep_ts,
            confirmation_ts_ns=confirmation_ts,
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "INSUFFICIENT_LEADERSHIP_HISTORY")

    def test_asynchronous_confirmation_fails_closed(self) -> None:
        gate, sweep_ts = self._ready_gate()
        self._batch(
            gate,
            4,
            {symbol: 101.0 for symbol in self.SYMBOLS},
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        result = gate.decide(
            symbol="BTCUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=sweep_ts,
            confirmation_ts_ns=5 * MINUTE_NS,
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "ASYNCHRONOUS_CONFIRMATION")


if __name__ == "__main__":
    unittest.main()
