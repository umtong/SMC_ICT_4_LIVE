from __future__ import annotations

import unittest

from market_leadership import MINUTE_NS, MarketLeadershipGate


class PriceDiscoveryStateTests(unittest.TestCase):
    SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    NOTIONALS = {
        "BTCUSDT": 5_000.0,
        "ETHUSDT": 4_000.0,
        "SOLUSDT": 3_000.0,
        "XRPUSDT": 2_000.0,
    }

    def observe(self, gate, minute, closes, notionals=None):
        notionals = notionals or self.NOTIONALS
        ts = minute * MINUTE_NS
        gate.observe_batch(
            ts,
            {
                symbol: (closes[symbol], notionals[symbol] / closes[symbol])
                for symbol in self.SYMBOLS
            },
        )
        return ts

    def test_leader_event_recovery_cannot_override_absolute_worst_trailing_rank(self):
        notionals = dict(self.NOTIONALS)
        notionals["ETHUSDT"] = 9_000.0
        gate = MarketLeadershipGate(
            self.SYMBOLS,
            lookback_bars=3,
            max_history_bars=20,
            confirmation_impulse_lookback_bars=2,
        )
        self.observe(gate, 1, {s: 100.0 for s in self.SYMBOLS}, notionals)
        self.observe(gate, 2, {
            "BTCUSDT": 101.0, "ETHUSDT": 99.0,
            "SOLUSDT": 102.0, "XRPUSDT": 103.0,
        }, notionals)
        sweep = self.observe(gate, 3, {
            "BTCUSDT": 102.0, "ETHUSDT": 98.0,
            "SOLUSDT": 104.0, "XRPUSDT": 106.0,
        }, notionals)
        confirm = self.observe(gate, 4, {
            "BTCUSDT": 102.1, "ETHUSDT": 100.0,
            "SOLUSDT": 104.1, "XRPUSDT": 106.1,
        }, notionals)
        result = gate.decide(
            symbol="ETHUSDT", scenario="FAR", direction="LONG",
            sweep_ts_ns=sweep, confirmation_ts_ns=confirm,
        )
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "LEADER_EVENT_RECOVERY_WITHOUT_DIRECTIONAL_SUPPORT",
        )
        self.assertEqual(result.trailing_direction_rank, 4)
        self.assertGreater(result.candidate_event_move, result.peer_event_median)

    def test_leader_aac_is_current_event_acceptance_not_stale_rank(self):
        notionals = dict(self.NOTIONALS)
        notionals["ETHUSDT"] = 9_000.0
        gate = MarketLeadershipGate(
            self.SYMBOLS,
            lookback_bars=3,
            max_history_bars=20,
            confirmation_impulse_lookback_bars=2,
        )
        self.observe(gate, 1, {s: 100.0 for s in self.SYMBOLS}, notionals)
        self.observe(gate, 2, {
            "BTCUSDT": 101.0, "ETHUSDT": 99.0,
            "SOLUSDT": 102.0, "XRPUSDT": 103.0,
        }, notionals)
        sweep = self.observe(gate, 3, {
            "BTCUSDT": 102.0, "ETHUSDT": 98.0,
            "SOLUSDT": 104.0, "XRPUSDT": 106.0,
        }, notionals)
        confirm = self.observe(gate, 4, {
            "BTCUSDT": 102.1, "ETHUSDT": 100.0,
            "SOLUSDT": 104.1, "XRPUSDT": 106.1,
        }, notionals)
        result = gate.decide(
            symbol="ETHUSDT", scenario="AAC", direction="LONG",
            sweep_ts_ns=sweep, confirmation_ts_ns=confirm,
        )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "LEADER_AAC_EVENT_ACCEPTANCE")
        self.assertEqual(result.trailing_direction_rank, 4)
        self.assertGreater(result.candidate_event_move, result.peer_event_median)

    def test_unanimous_peers_reject_event_time_laggard(self):
        gate = MarketLeadershipGate(
            self.SYMBOLS,
            lookback_bars=5,
            max_history_bars=30,
            confirmation_impulse_lookback_bars=3,
        )
        paths = [
            {s: 100.0 for s in self.SYMBOLS},
            {s: 100.1 for s in self.SYMBOLS},
            {s: 100.0 for s in self.SYMBOLS},
            {s: 100.1 for s in self.SYMBOLS},
            {s: 100.0 for s in self.SYMBOLS},
        ]
        for minute, closes in enumerate(paths, 1):
            self.observe(gate, minute, closes)
        sweep = 5 * MINUTE_NS
        confirm = self.observe(gate, 6, {
            "BTCUSDT": 101.0, "ETHUSDT": 100.9,
            "SOLUSDT": 100.2, "XRPUSDT": 100.8,
        })
        result = gate.decide(
            symbol="SOLUSDT", scenario="FAR", direction="LONG",
            sweep_ts_ns=sweep, confirmation_ts_ns=confirm,
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "FOLLOWER_FAR_EVENT_LAGGARD")
        self.assertEqual(result.event_direction_rank, 4)
        self.assertGreater(result.confirmation_impulse, 1.0)

    def _idiosyncratic_gate(self, weak=False):
        gate = MarketLeadershipGate(
            self.SYMBOLS,
            lookback_bars=6,
            max_history_bars=40,
            confirmation_impulse_lookback_bars=3,
            minimum_idiosyncratic_event_efficiency=0.10,
            minimum_idiosyncratic_event_displacement=0.50,
        )
        pre = [
            {"BTCUSDT":100.0,"ETHUSDT":100.0,"SOLUSDT":100.0,"XRPUSDT":100.0},
            {"BTCUSDT":100.0,"ETHUSDT":100.0,"SOLUSDT":100.2,"XRPUSDT":99.9},
            {"BTCUSDT":100.1,"ETHUSDT":99.9,"SOLUSDT":100.4,"XRPUSDT":99.8},
            {"BTCUSDT":100.0,"ETHUSDT":100.0,"SOLUSDT":100.6,"XRPUSDT":99.9},
            {"BTCUSDT":100.1,"ETHUSDT":99.9,"SOLUSDT":100.8,"XRPUSDT":99.8},
            {"BTCUSDT":100.0,"ETHUSDT":100.0,"SOLUSDT":101.0,"XRPUSDT":99.9},
        ]
        for minute, closes in enumerate(pre, 1):
            self.observe(gate, minute, closes)
        sweep = 6 * MINUTE_NS
        if weak:
            self.observe(gate, 7, {
                "BTCUSDT":99.9,"ETHUSDT":99.9,"SOLUSDT":102.0,"XRPUSDT":99.8,
            })
            confirm = self.observe(gate, 8, {
                "BTCUSDT":99.8,"ETHUSDT":99.9,"SOLUSDT":101.1,"XRPUSDT":99.7,
            })
        else:
            self.observe(gate, 7, {
                "BTCUSDT":99.9,"ETHUSDT":99.9,"SOLUSDT":101.4,"XRPUSDT":99.8,
            })
            confirm = self.observe(gate, 8, {
                "BTCUSDT":99.8,"ETHUSDT":99.9,"SOLUSDT":102.0,"XRPUSDT":99.7,
            })
        return gate, sweep, confirm

    def test_idiosyncratic_price_discovery_approves_efficient_rank_one_event(self):
        gate, sweep, confirm = self._idiosyncratic_gate(weak=False)
        result = gate.decide(
            symbol="SOLUSDT", scenario="FAR", direction="LONG",
            sweep_ts_ns=sweep, confirmation_ts_ns=confirm,
        )
        self.assertTrue(result.approved)
        self.assertEqual(
            result.reason,
            "FOLLOWER_FAR_IDIOSYNCRATIC_PRICE_DISCOVERY",
        )
        self.assertEqual(result.trailing_direction_rank, 1)
        self.assertEqual(result.event_direction_rank, 1)
        self.assertGreaterEqual(result.event_path_efficiency, 0.10)
        self.assertGreaterEqual(result.event_standardized_displacement, 0.50)

    def test_idiosyncratic_rank_one_rejects_inefficient_reversal_path(self):
        gate, sweep, confirm = self._idiosyncratic_gate(weak=True)
        result = gate.decide(
            symbol="SOLUSDT", scenario="FAR", direction="LONG",
            sweep_ts_ns=sweep, confirmation_ts_ns=confirm,
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "FOLLOWER_FAR_PEER_DISAGREEMENT")
        self.assertEqual(result.trailing_direction_rank, 1)
        self.assertEqual(result.event_direction_rank, 1)
        self.assertTrue(
            result.event_path_efficiency < 0.10
            or result.event_standardized_displacement < 0.50,
        )


if __name__ == "__main__":
    unittest.main()
