import unittest
from complex_engine import (
    AuctionContext,
    BarObs,
    ComplexSCDAMEngine,
    Direction,
    EngineConfig,
    MINUTE_NS,
    Scenario,
    SYMBOLS,
)


def bar(symbol, index, open_, high, low, close, flow=0.0):
    return BarObs(
        symbol,
        index * MINUTE_NS,
        open_,
        high,
        low,
        close,
        100.0,
        50.0 * (flow + 1.0),
    )


def context(end=200):
    return AuctionContext("ASIA", "LONDON", 90.0, 100.0, 80.0, 120.0, end * MINUTE_NS)


class ComplexEngineTests(unittest.TestCase):
    def warm(self, engine):
        for index in range(31):
            snapshot = {symbol: bar(symbol, index, 95, 96, 94, 95) for symbol in SYMBOLS}
            engine.on_snapshot(snapshot, {symbol: context() for symbol in SYMBOLS})

    def test_snapshot_time_must_be_synchronous(self):
        engine = ComplexSCDAMEngine()
        with self.assertRaisesRegex(ValueError, "one completed timestamp"):
            engine.on_snapshot(
                {
                    "BTCUSDT": bar("BTCUSDT", 1, 95, 96, 94, 95),
                    "ETHUSDT": bar("ETHUSDT", 2, 95, 96, 94, 95),
                },
                {},
            )

    def test_far_requires_nonconfirmation_then_causal_pivot_break(self):
        engine = ComplexSCDAMEngine(EngineConfig(min_net_r=0.1))
        self.warm(engine)
        contexts = {symbol: context() for symbol in SYMBOLS}
        snapshot = {symbol: bar(symbol, 31, 95, 99, 94, 95) for symbol in SYMBOLS}
        snapshot["BTCUSDT"] = bar("BTCUSDT", 31, 99, 103, 98, 99, 0.4)
        self.assertEqual(engine.on_snapshot(snapshot, contexts), [])
        for index, low, close in ((32, 97, 98), (33, 96, 97), (34, 97, 98)):
            snapshot = {symbol: bar(symbol, index, 95, 99, 94, 95) for symbol in SYMBOLS}
            snapshot["BTCUSDT"] = bar("BTCUSDT", index, 98, 99, low, close, -0.1)
            self.assertEqual(engine.on_snapshot(snapshot, contexts), [])
        snapshot = {symbol: bar(symbol, 35, 95, 99, 94, 95) for symbol in SYMBOLS}
        snapshot["BTCUSDT"] = bar("BTCUSDT", 35, 98, 98.2, 94, 95, -0.6)
        plans = engine.on_snapshot(snapshot, contexts)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].scenario, Scenario.FAR)
        self.assertEqual(plans[0].direction, Direction.SHORT)

    def test_aac_uses_breadth_and_separate_frozen_impulse(self):
        engine = ComplexSCDAMEngine(
            EngineConfig(min_net_r=0.1, min_displacement_atr=0.05),
        )
        self.warm(engine)
        contexts = {symbol: context() for symbol in SYMBOLS}
        for index in (31, 32):
            snapshot = {symbol: bar(symbol, index, 100, 103, 100, 102, 0.3) for symbol in SYMBOLS}
            self.assertEqual(engine.on_snapshot(snapshot, contexts), [])
        for index, low, close in ((33, 101.8, 102), (34, 101.4, 101.8), (35, 101.7, 102)):
            snapshot = {symbol: bar(symbol, index, 101.8, 103, low, close, 0.0) for symbol in SYMBOLS}
            engine.on_snapshot(snapshot, contexts)
        snapshot = {symbol: bar(symbol, 36, 102, 104, 101.8, 103.5, 0.5) for symbol in SYMBOLS}
        plans = engine.on_snapshot(snapshot, contexts)
        self.assertTrue(any(plan.scenario == Scenario.AAC for plan in plans))
        plan = next(plan for plan in plans if plan.scenario == Scenario.AAC)
        self.assertLess(plan.stop_price, 100)


if __name__ == "__main__":
    unittest.main()
