from __future__ import annotations

import unittest

from market_leadership import MINUTE_NS, MarketLeadershipGate


class MarketLeadershipGateTests(unittest.TestCase):
    SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")

    @staticmethod
    def _batch(gate, minute, closes, notionals):
        ts_ns = minute * MINUTE_NS
        gate.observe_batch(
            ts_ns,
            {
                symbol: (closes[symbol], notionals[symbol] / closes[symbol])
                for symbol in gate.symbols
            },
        )
        return ts_ns

    def _ready_gate(self, *, lookback=3, closes_by_minute=None):
        gate = MarketLeadershipGate(
            self.SYMBOLS,
            lookback_bars=lookback,
            max_history_bars=32,
        )
        notionals = {
            "BTCUSDT": 1000.0,
            "ETHUSDT": 5000.0,
            "SOLUSDT": 900.0,
            "XRPUSDT": 800.0,
        }
        for minute in range(1, lookback + 1):
            closes = (
                closes_by_minute[minute]
                if closes_by_minute is not None
                else {symbol: 100.0 for symbol in self.SYMBOLS}
            )
            self._batch(gate, minute, closes, notionals)
        return gate, lookback * MINUTE_NS

    def test_leader_aac_requires_directional_acceptance(self):
        gate, sweep_ts = self._ready_gate()
        confirmation_ts = self._batch(
            gate,
            4,
            {
                "BTCUSDT": 101.0,
                "ETHUSDT": 102.0,
                "SOLUSDT": 101.0,
                "XRPUSDT": 101.0,
            },
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        approved = gate.decide(
            symbol="ETHUSDT",
            scenario="AAC",
            direction="LONG",
            sweep_ts_ns=sweep_ts,
            confirmation_ts_ns=confirmation_ts,
        )
        self.assertTrue(approved.approved)
        self.assertEqual(approved.reason, "LEADER_AAC_DIRECTIONAL_ACCEPTANCE")

        gate2 = MarketLeadershipGate(
            self.SYMBOLS,
            lookback_bars=3,
            max_history_bars=12,
        )
        notionals = {
            "BTCUSDT": 1000.0,
            "ETHUSDT": 5000.0,
            "SOLUSDT": 900.0,
            "XRPUSDT": 800.0,
        }
        for minute, closes in {
            1: {symbol: 100.0 for symbol in self.SYMBOLS},
            2: {
                "BTCUSDT": 100.5,
                "ETHUSDT": 100.5,
                "SOLUSDT": 100.5,
                "XRPUSDT": 100.5,
            },
            3: {
                "BTCUSDT": 101.0,
                "ETHUSDT": 101.0,
                "SOLUSDT": 101.0,
                "XRPUSDT": 101.0,
            },
        }.items():
            self._batch(gate2, minute, closes, notionals)
        rejected_ts = self._batch(
            gate2,
            4,
            {
                "BTCUSDT": 103.0,
                "ETHUSDT": 101.1,
                "SOLUSDT": 103.0,
                "XRPUSDT": 103.0,
            },
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        rejected = gate2.decide(
            symbol="ETHUSDT",
            scenario="AAC",
            direction="LONG",
            sweep_ts_ns=3 * MINUTE_NS,
            confirmation_ts_ns=rejected_ts,
        )
        self.assertFalse(rejected.approved)
        self.assertEqual(rejected.reason, "AAC_WITHOUT_DIRECTIONAL_ACCEPTANCE")

    def test_notional_leader_cannot_bypass_directional_evidence(self):
        closes = {
            1: {
                "BTCUSDT": 100.0,
                "ETHUSDT": 100.0,
                "SOLUSDT": 100.0,
                "XRPUSDT": 100.0,
            },
            2: {
                "BTCUSDT": 101.0,
                "ETHUSDT": 99.0,
                "SOLUSDT": 102.0,
                "XRPUSDT": 103.0,
            },
            3: {
                "BTCUSDT": 102.0,
                "ETHUSDT": 98.0,
                "SOLUSDT": 104.0,
                "XRPUSDT": 106.0,
            },
        }
        gate, sweep_ts = self._ready_gate(closes_by_minute=closes)
        confirmation_ts = self._batch(
            gate,
            4,
            {
                "BTCUSDT": 103.0,
                "ETHUSDT": 98.1,
                "SOLUSDT": 105.0,
                "XRPUSDT": 107.0,
            },
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        laggard = gate.decide(
            symbol="ETHUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=sweep_ts,
            confirmation_ts_ns=confirmation_ts,
        )
        self.assertFalse(laggard.approved)
        self.assertEqual(laggard.reason, "LEADER_DIRECTIONAL_DISAGREEMENT")

    def test_leader_event_recovery_can_confirm_low_trailing_rank(self):
        closes = {
            1: {
                "BTCUSDT": 100.0,
                "ETHUSDT": 100.0,
                "SOLUSDT": 100.0,
                "XRPUSDT": 100.0,
            },
            2: {
                "BTCUSDT": 99.0,
                "ETHUSDT": 101.0,
                "SOLUSDT": 102.0,
                "XRPUSDT": 103.0,
            },
            3: {
                "BTCUSDT": 98.0,
                "ETHUSDT": 102.0,
                "SOLUSDT": 104.0,
                "XRPUSDT": 106.0,
            },
        }
        gate, sweep_ts = self._ready_gate(closes_by_minute=closes)
        confirmation_ts = self._batch(
            gate,
            4,
            {
                "BTCUSDT": 101.0,
                "ETHUSDT": 102.2,
                "SOLUSDT": 104.2,
                "XRPUSDT": 106.2,
            },
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        result = gate.decide(
            symbol="ETHUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=sweep_ts,
            confirmation_ts_ns=confirmation_ts,
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "LEADER_EVENT_RECOVERY")

    def test_unanimous_follower_passes_in_moderate_adverse_regime(self):
        closes = {
            1: {symbol: 100.0 for symbol in self.SYMBOLS},
            2: {
                "BTCUSDT": 99.8,
                "ETHUSDT": 99.7,
                "SOLUSDT": 99.5,
                "XRPUSDT": 99.6,
            },
            3: {
                "BTCUSDT": 99.7,
                "ETHUSDT": 99.6,
                "SOLUSDT": 99.4,
                "XRPUSDT": 99.5,
            },
        }
        gate, sweep_ts = self._ready_gate(closes_by_minute=closes)
        confirmation_ts = self._batch(
            gate,
            4,
            {
                "BTCUSDT": 100.1,
                "ETHUSDT": 100.0,
                "SOLUSDT": 100.2,
                "XRPUSDT": 99.9,
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

    def test_unanimous_countertrend_bounce_rejected_in_unresolved_adverse_auction(self):
        gate = MarketLeadershipGate(
            self.SYMBOLS,
            lookback_bars=6,
            max_history_bars=32,
            severe_adverse_trend_score=-1.5,
        )
        notionals = {
            "BTCUSDT": 1000.0,
            "ETHUSDT": 5000.0,
            "SOLUSDT": 900.0,
            "XRPUSDT": 800.0,
        }
        for minute in range(1, 7):
            base = 100.0 - 2.0 * (minute - 1)
            closes = {
                "BTCUSDT": base,
                "ETHUSDT": base - 0.2,
                "SOLUSDT": base - 0.4,
                "XRPUSDT": base - 0.1,
            }
            self._batch(gate, minute, closes, notionals)
        sweep_ts = 6 * MINUTE_NS
        confirmation_ts = self._batch(
            gate,
            7,
            {
                "BTCUSDT": 91.0,
                "ETHUSDT": 90.8,
                "SOLUSDT": 91.2,
                "XRPUSDT": 90.9,
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
        self.assertEqual(
            result.reason,
            "FOLLOWER_FAR_UNRESOLVED_ADVERSE_AUCTION",
        )

    def test_directional_recovery_needs_at_least_one_peer_confirmation(self):
        closes = {
            1: {symbol: 100.0 for symbol in self.SYMBOLS},
            2: {
                "BTCUSDT": 100.0,
                "ETHUSDT": 100.0,
                "SOLUSDT": 102.0,
                "XRPUSDT": 99.5,
            },
            3: {
                "BTCUSDT": 100.0,
                "ETHUSDT": 100.0,
                "SOLUSDT": 104.0,
                "XRPUSDT": 99.0,
            },
        }
        gate, sweep_ts = self._ready_gate(closes_by_minute=closes)
        confirmation_ts = self._batch(
            gate,
            4,
            {
                "BTCUSDT": 99.5,
                "ETHUSDT": 99.5,
                "SOLUSDT": 105.0,
                "XRPUSDT": 98.5,
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

    def test_directional_recovery_with_one_peer_confirmation_passes(self):
        closes = {
            1: {symbol: 100.0 for symbol in self.SYMBOLS},
            2: {
                "BTCUSDT": 100.0,
                "ETHUSDT": 100.0,
                "SOLUSDT": 102.0,
                "XRPUSDT": 99.5,
            },
            3: {
                "BTCUSDT": 100.0,
                "ETHUSDT": 100.0,
                "SOLUSDT": 104.0,
                "XRPUSDT": 99.0,
            },
        }
        gate, sweep_ts = self._ready_gate(closes_by_minute=closes)
        confirmation_ts = self._batch(
            gate,
            4,
            {
                "BTCUSDT": 100.5,
                "ETHUSDT": 99.5,
                "SOLUSDT": 105.5,
                "XRPUSDT": 98.5,
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
        self.assertEqual(
            result.reason,
            "FOLLOWER_FAR_DIRECTIONAL_LEADER_RECOVERY",
        )

    def test_missing_history_and_async_confirmation_fail_closed(self):
        gate = MarketLeadershipGate(
            self.SYMBOLS,
            lookback_bars=3,
            max_history_bars=8,
        )
        self._batch(
            gate,
            1,
            {symbol: 100.0 for symbol in self.SYMBOLS},
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        confirmation_ts = self._batch(
            gate,
            2,
            {symbol: 101.0 for symbol in self.SYMBOLS},
            {symbol: 1000.0 for symbol in self.SYMBOLS},
        )
        missing = gate.decide(
            symbol="BTCUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=MINUTE_NS,
            confirmation_ts_ns=confirmation_ts,
        )
        self.assertFalse(missing.approved)
        self.assertEqual(missing.reason, "INSUFFICIENT_LEADERSHIP_HISTORY")
        asynchronous = gate.decide(
            symbol="BTCUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=MINUTE_NS,
            confirmation_ts_ns=3 * MINUTE_NS,
        )
        self.assertFalse(asynchronous.approved)
        self.assertEqual(asynchronous.reason, "ASYNCHRONOUS_CONFIRMATION")


if __name__ == "__main__":
    unittest.main()
