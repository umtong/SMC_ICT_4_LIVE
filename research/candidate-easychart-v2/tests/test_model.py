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
        self.assertEqual(engine._latest_origin(Side.LONG, before_ns=10), 100.0)

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
        self.assertGreaterEqual(plan.gross_rr, 1.0)


if __name__ == "__main__":
    unittest.main()
