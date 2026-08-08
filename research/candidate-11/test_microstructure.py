from __future__ import annotations

import unittest

from microstructure import (
    SECOND_NS,
    AggressorImpactAuctionEngine,
    FlowBar,
)


class MicrostructureTests(unittest.TestCase):
    @staticmethod
    def bar(index: int, price: float = 100.0, signed_notional: float = 0.0) -> FlowBar:
        buy_quote = max(signed_notional, 0.0)
        sell_quote = max(-signed_notional, 0.0)
        quote = max(1000.0, buy_quote + sell_quote)
        volume = quote / price
        buy_volume = buy_quote / price
        sell_volume = sell_quote / price
        if signed_notional == 0:
            buy_volume = sell_volume = volume / 2
        return FlowBar(
            ts_ns=(index + 1) * SECOND_NS,
            open=price,
            high=price + 0.01,
            low=price - 0.01,
            close=price,
            volume=volume,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            quote_notional=quote,
            signed_notional=signed_notional,
            trade_count=10,
            max_trade_notional=quote / 10,
        )

    def test_completed_five_minute_boundary_is_not_visible_early(self) -> None:
        engine = AggressorImpactAuctionEngine("BTCUSDT-PERP.BINANCE")
        for index in range(300):
            engine.on_bar(self.bar(index, 100.0 + index * 0.0001))
        self.assertEqual([], [pool for pool in engine.pools if pool.source == "PRIOR_5M_AUCTION"])
        engine.on_bar(self.bar(300, 100.03))
        pools = [pool for pool in engine.pools if pool.source == "PRIOR_5M_AUCTION"]
        self.assertEqual(2, len(pools))
        self.assertTrue(all(pool.created_ts_ns == 301 * SECOND_NS for pool in pools))

    def test_non_monotonic_bars_fail_closed(self) -> None:
        engine = AggressorImpactAuctionEngine("BTCUSDT-PERP.BINANCE")
        engine.on_bar(self.bar(0))
        with self.assertRaises(ValueError):
            engine.on_bar(self.bar(0))

    def test_costed_plan_rejects_wrong_price_order(self) -> None:
        engine = AggressorImpactAuctionEngine("BTCUSDT-PERP.BINANCE")
        plan = engine._costed_plan(
            scenario="AR",
            direction="LONG",
            observed_ts_ns=10 * SECOND_NS,
            entry=100.0,
            stop=101.0,
            target=103.0,
            expiry_seconds=20,
            details={},
        )
        self.assertIsNone(plan)
        self.assertEqual(1, engine.skips["NON_CAUSAL_PRICE_ORDER"])

    def test_costed_plan_includes_conservative_friction(self) -> None:
        engine = AggressorImpactAuctionEngine("BTCUSDT-PERP.BINANCE")
        plan = engine._costed_plan(
            scenario="AR",
            direction="LONG",
            observed_ts_ns=10 * SECOND_NS,
            entry=100.0,
            stop=99.0,
            target=104.0,
            expiry_seconds=20,
            details={"source": "TEST"},
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertGreater(plan.loss_per_unit, 1.0)
        self.assertGreaterEqual(plan.net_r, 1.25)
        self.assertEqual(30 * SECOND_NS, plan.expire_ts_ns)

    def test_current_flow_is_excluded_from_its_baseline(self) -> None:
        engine = AggressorImpactAuctionEngine("BTCUSDT-PERP.BINANCE")
        for index in range(901):
            engine.bars.append(self.bar(index, 100.0, signed_notional=1000.0))
        engine.bars.append(self.bar(901, 100.0, signed_notional=1_000_000.0))
        rms = engine._flow_rms(lookback=900)
        self.assertIsNotNone(rms)
        assert rms is not None
        self.assertAlmostEqual(1000.0, rms, places=6)


if __name__ == "__main__":
    unittest.main()
