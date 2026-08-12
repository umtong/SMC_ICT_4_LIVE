from __future__ import annotations

import unittest

from model import Boundary, Candle, EasyChartStateEngine, EngineConfig, Family, Side


class EasyChartStateEngineTest(unittest.TestCase):
    def bar(self, index: int, open_: float, high: float, low: float, close: float) -> Candle:
        return Candle(index * 300_000_000_000, open_, high, low, close, 1.0)

    def test_pivot_is_not_observable_before_right_window_closes(self) -> None:
        engine = EasyChartStateEngine(
            "BTCUSDT",
            EngineConfig(pivot_spans=(2,), atr_period=2, min_prominence_atr=0.5),
        )
        bars = [
            self.bar(1, 10, 11, 9, 10),
            self.bar(2, 10, 12, 9.5, 11),
            self.bar(3, 11, 15, 10, 12),
            self.bar(4, 12, 13, 10.5, 11),
            self.bar(5, 11, 12, 10, 11),
        ]
        for bar in bars[:4]:
            engine.on_bar(bar)
        self.assertFalse(any(boundary.level == 15 for boundary in engine.boundaries))
        engine.on_bar(bars[4])
        self.assertTrue(any(boundary.level == 15 for boundary in engine.boundaries))

    def test_acceptance_origin_can_form_after_source_boundary_but_before_break(self) -> None:
        engine = EasyChartStateEngine("BTCUSDT", EngineConfig())
        source = Boundary("high", "HIGH", 110.0, 1, 2, 6, 2.0)
        later_origin = Boundary("low", "LOW", 100.0, 3, 5, 6, 2.0)
        engine.boundaries.extend([source, later_origin])
        origin = engine._latest_origin(Side.LONG, before_ns=10, min_span=source.span)
        self.assertIsNotNone(origin)
        assert origin is not None
        self.assertEqual(origin.level, 100.0)

    def test_acceptance_origin_must_match_source_auction_scale(self) -> None:
        engine = EasyChartStateEngine("BTCUSDT", EngineConfig())
        large_origin = Boundary("large", "LOW", 95.0, 1, 3, 6, 2.0)
        recent_small_origin = Boundary("small", "LOW", 99.0, 4, 8, 2, 4.0)
        engine.boundaries.extend([large_origin, recent_small_origin])
        origin = engine._latest_origin(Side.LONG, before_ns=10, min_span=6)
        self.assertIs(origin, large_origin)

    def test_rejection_requires_confirmation_then_first_close_held_retest(self) -> None:
        engine = EasyChartStateEngine(
            "BTCUSDT",
            EngineConfig(
                pivot_spans=(50,),
                atr_period=2,
                min_prominence_atr=0.1,
                min_gross_rr=1.0,
                tick_size=0.1,
                enable_acceptance=False,
            ),
        )
        engine.boundaries.extend(
            [
                Boundary("source", "LOW", 100.0, 0, 0, 12, 3.0),
                Boundary("target", "HIGH", 110.0, 0, 0, 12, 3.0),
            ],
        )
        bars = [
            self.bar(1, 101, 102, 100.5, 101),
            self.bar(2, 101, 102, 99, 100.5),   # sweep and reclaim: arm
            self.bar(3, 100.5, 103, 100.4, 102), # displacement: confirm
            self.bar(4, 102, 104, 101, 103),
            self.bar(5, 103, 104, 99.9, 101),    # first retest closes inside
        ]
        plans = []
        for bar in bars:
            plans.extend(engine.on_bar(bar))
        rejections = [plan for plan in plans if plan.family is Family.REJECTION_RETEST_CLOSE]
        self.assertEqual(len(rejections), 1)
        plan = rejections[0]
        self.assertIs(plan.side, Side.LONG)
        self.assertEqual(plan.entry, bars[-1].close)
        self.assertEqual(plan.stop, 98.9)
        self.assertEqual(plan.target, 110.0)
        self.assertEqual(plan.interaction_time_ns, bars[1].ts_close_ns)
        self.assertEqual(plan.confirmation_time_ns, bars[2].ts_close_ns)
        self.assertEqual(plan.trigger_extreme, 99.0)
        self.assertGreaterEqual(plan.gross_rr, 1.0)

    def test_acceptance_requires_outside_hold_then_first_retest(self) -> None:
        engine = EasyChartStateEngine(
            "BTCUSDT",
            EngineConfig(
                pivot_spans=(50,),
                atr_period=2,
                min_prominence_atr=0.1,
                min_gross_rr=1.0,
                tick_size=0.1,
                enable_rejection=False,
            ),
        )
        origin = Boundary("origin", "LOW", 95.0, 0, 0, 12, 3.0)
        source = Boundary("source", "HIGH", 100.0, 0, 0, 12, 3.0)
        target = Boundary("target", "HIGH", 110.0, 0, 0, 12, 3.0)
        engine.boundaries.extend([origin, source, target])
        bars = [
            self.bar(1, 97, 99, 96, 98),
            self.bar(2, 98, 103, 97, 102),       # body break: arm
            self.bar(3, 101, 104, 100.5, 103),   # next bar opens/closes outside: confirm
            self.bar(4, 103, 104, 101, 102),
            self.bar(5, 102, 103, 99.8, 101),    # first retest, close remains outside
        ]
        plans = []
        for bar in bars:
            plans.extend(engine.on_bar(bar))
        accepted = [plan for plan in plans if plan.family is Family.ACCEPTANCE_RETEST_CLOSE]
        self.assertEqual(len(accepted), 1)
        plan = accepted[0]
        self.assertIs(plan.side, Side.LONG)
        self.assertEqual(plan.entry, 101.0)
        self.assertEqual(plan.stop, 94.9)
        self.assertEqual(plan.target, 110.0)
        self.assertEqual(plan.origin_boundary_id, origin.boundary_id)
        self.assertEqual(plan.interaction_time_ns, bars[1].ts_close_ns)
        self.assertEqual(plan.confirmation_time_ns, bars[2].ts_close_ns)


if __name__ == "__main__":
    unittest.main()
