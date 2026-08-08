from __future__ import annotations

import os
import unittest

from logic import LogicConfig, MINUTE_NS, Side

from c10_v50_state import (
    InternalDealingRangeFailedAuctionEngine,
    internal_dealing_range_enabled,
)


class InternalDealingRangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("C10_V50_INTERNAL_DEALING_RANGE")
        os.environ["C10_V50_INTERNAL_DEALING_RANGE"] = "1"
        self.engine = InternalDealingRangeFailedAuctionEngine(
            LogicConfig(),
            "BTCUSDT-PERP.BINANCE",
        )
        self.engine._index = 100

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("C10_V50_INTERNAL_DEALING_RANGE", None)
        else:
            os.environ["C10_V50_INTERNAL_DEALING_RANGE"] = self.previous

    def test_environment_ablation_is_exact(self) -> None:
        self.assertTrue(internal_dealing_range_enabled())
        os.environ["C10_V50_INTERNAL_DEALING_RANGE"] = "0"
        self.assertFalse(internal_dealing_range_enabled())

    def test_ambiguous_episodes_receive_distinct_evidence_ids(self) -> None:
        for observed in (100, 200):
            self.engine._event(
                "AMBIGUOUS",
                "AMBIGUOUS_SWEEP",
                observed,
                observed,
                "ARMED",
                "TERMINAL",
                "BAR_PATH_UNRESOLVABLE",
            )
        self.assertEqual(len(self.engine.events), 2)
        first = self.engine.events[0].scenario_id
        second = self.engine.events[1].scenario_id
        self.assertNotEqual(first, second)
        self.assertIn("AMBIGUOUS", first)
        self.assertIn("AMBIGUOUS", second)

    def test_high_endpoint_pairs_only_with_preexisting_low(self) -> None:
        self.engine.internal_lows.append((10 * MINUTE_NS, 12 * MINUTE_NS, 90.0))
        self.engine._add_internal_endpoint(
            side=Side.HIGH,
            event_ts_ns=20 * MINUTE_NS,
            known_ts_ns=22 * MINUTE_NS,
            level=100.0,
        )
        self.assertEqual(len(self.engine.pools), 1)
        pool = self.engine.pools[0]
        self.assertEqual(pool.side, Side.HIGH)
        self.assertEqual(pool.level, 100.0)
        self.assertEqual(pool.opposite_level, 90.0)
        self.assertEqual(pool.source, "CONFIRMED_INTERNAL_5M_DEALING_RANGE")
        self.assertEqual(pool.expiry_index, 100 + self.engine.config.event_expiry_bars)
        self.assertGreater(pool.trigger_end_ts_ns, pool.confirmed_ts_ns)

    def test_same_time_or_future_opposite_cannot_define_range(self) -> None:
        self.engine.internal_lows.extend([
            (10 * MINUTE_NS, 22 * MINUTE_NS, 90.0),
            (11 * MINUTE_NS, 23 * MINUTE_NS, 89.0),
        ])
        self.engine._add_internal_endpoint(
            side=Side.HIGH,
            event_ts_ns=20 * MINUTE_NS,
            known_ts_ns=22 * MINUTE_NS,
            level=100.0,
        )
        self.assertEqual(len(self.engine.pools), 0)
        self.assertEqual(
            self.engine.skips["V50_NO_PREEXISTING_OPPOSITE_INTERNAL_PIVOT"],
            1,
        )

    def test_stale_opposite_pivot_is_rejected(self) -> None:
        horizon = self.engine.config.event_expiry_bars * MINUTE_NS
        self.engine.internal_highs.append((1, 10, 110.0))
        self.engine._add_internal_endpoint(
            side=Side.LOW,
            event_ts_ns=horizon + 100,
            known_ts_ns=horizon + 20,
            level=100.0,
        )
        self.assertEqual(len(self.engine.pools), 0)

    def test_price_order_must_form_a_real_range(self) -> None:
        self.engine.internal_highs.append((10, 20, 100.0))
        self.engine._add_internal_endpoint(
            side=Side.LOW,
            event_ts_ns=30,
            known_ts_ns=40,
            level=101.0,
        )
        self.assertEqual(len(self.engine.pools), 0)
        self.assertEqual(
            self.engine.skips["V50_INVALID_INTERNAL_RANGE_PRICE_ORDER"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
