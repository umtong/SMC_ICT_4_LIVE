from __future__ import annotations

import unittest

from domain import Candle, Side
from trendline_retest_scenario import (
    TrendlineFirstRetestScenarioEngine,
    TrendlineRetestState,
)


class TrendlineFirstRetestScenarioEngineTest(unittest.TestCase):
    def bar(
        self,
        index: int,
        open_: float,
        high: float,
        low: float,
        close: float,
    ) -> Candle:
        return Candle(index * 60_000_000_000, open_, high, low, close, 1.0)

    def engine(self) -> TrendlineFirstRetestScenarioEngine:
        return TrendlineFirstRetestScenarioEngine(
            "BTCUSDT",
            5,
            0.1,
            minimum_gross_rr=1.0,
            swing_span=1,
            min_anchor_bars=3,
            tolerance_range_fraction=0.0,
        )

    def pre_break_bars(self) -> list[Candle]:
        # Confirmed falling wick highs at indices 1 (12.0) and 4 (11.0).
        return [
            self.bar(0, 9.5, 10.0, 9.0, 9.5),
            self.bar(1, 11.0, 12.0, 10.5, 11.5),
            self.bar(2, 10.5, 11.0, 10.0, 10.5),
            self.bar(3, 10.0, 10.5, 9.8, 10.1),
            self.bar(4, 10.7, 11.0, 10.3, 10.8),
            self.bar(5, 10.2, 10.5, 9.9, 10.1),
            self.bar(6, 10.0, 10.2, 9.8, 10.0),
        ]

    def test_break_first_retest_and_later_ob_create_one_complete_plan(self) -> None:
        engine = self.engine()
        for bar in self.pre_break_bars():
            self.assertEqual(engine.on_bar(bar), [])

        # The break consumes the nearer old 11.0 high. Its own 11.8 impulse high
        # becomes observable on the retest and is the nearest pre-entry target;
        # the engine must not jump past it to the older 12.0 high.
        self.assertEqual(engine.on_bar(self.bar(7, 10.0, 11.8, 9.9, 11.2)), [])
        # First later retest closes on the breakout side; it is not yet an OB.
        self.assertEqual(engine.on_bar(self.bar(8, 10.2, 10.3, 9.6, 9.9)), [])
        self.assertEqual(len(engine.setups), 1)
        self.assertIs(
            engine.setups[0].state,
            TrendlineRetestState.WAITING_CONFIRMATION,
        )

        # The first strong bullish engulfing OB is formed from the retest bar.
        plans = engine.on_bar(self.bar(9, 9.8, 10.6, 9.5, 10.5))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertIs(plan.side, Side.LONG)
        self.assertEqual(plan.family, "TRENDLINE_BREAK_FIRST_RETEST_OB")
        self.assertAlmostEqual(plan.entry, 10.5)
        self.assertAlmostEqual(plan.stop, 9.4)
        self.assertAlmostEqual(plan.target, 11.8)
        self.assertGreaterEqual(plan.gross_rr, 1.0)
        self.assertEqual(plan.target_kind, "SWING_HIGH")
        self.assertGreaterEqual(plan.trigger_strength_ratio, 2.0)
        self.assertLess(plan.break_time_ns, plan.retest_time_ns)
        self.assertLessEqual(plan.retest_time_ns, plan.observed_time_ns)
        self.assertIs(engine.setups[0].state, TrendlineRetestState.PLANNED)

    def test_sweep_is_not_a_universal_prerequisite_for_this_family(self) -> None:
        engine = self.engine()
        bars = self.pre_break_bars() + [
            self.bar(7, 10.0, 11.8, 9.9, 11.2),
            self.bar(8, 10.2, 10.3, 9.6, 9.9),
            self.bar(9, 9.8, 10.6, 9.5, 10.5),
        ]
        plans = []
        for bar in bars:
            plans.extend(engine.on_bar(bar))
        self.assertEqual(len(plans), 1)
        # The engine has no mandatory local-sweep state; its causal lineage is
        # trendline break -> first retest -> OB confirmation.
        self.assertNotIn("sweep", plans[0].causal_event_id.lower())

    def test_nearest_low_rr_objective_blocks_skipping_to_farther_high(self) -> None:
        engine = self.engine()
        for bar in self.pre_break_bars():
            engine.on_bar(bar)
        # The close breaks the trendline and trades through the old 11.0 high.
        # The new 11.0 impulse high is causally known before entry but offers
        # less than 1R. The fixed contract rejects the trade instead of choosing
        # the farther 12.0 high to manufacture acceptable RR.
        engine.on_bar(self.bar(7, 10.0, 11.0, 9.9, 10.9))
        engine.on_bar(self.bar(8, 10.2, 10.3, 9.6, 9.9))
        plans = engine.on_bar(self.bar(9, 9.8, 10.6, 9.5, 10.5))
        self.assertEqual(plans, [])
        self.assertIs(engine.setups[0].state, TrendlineRetestState.RR_BELOW_MINIMUM)
        self.assertEqual(engine.diagnostics.get("trigger_rr_below_minimum"), 1)

    def test_weak_engulfing_at_retest_is_not_relabelled_as_strong_ob(self) -> None:
        engine = self.engine()
        for bar in self.pre_break_bars():
            engine.on_bar(bar)
        engine.on_bar(self.bar(7, 10.0, 11.8, 9.9, 11.2))
        engine.on_bar(self.bar(8, 10.2, 10.3, 9.6, 9.9))
        # Bullish engulfing, but body 0.45 versus prior 0.30 is below 2x.
        plans = engine.on_bar(self.bar(9, 9.85, 10.4, 9.5, 10.3))
        self.assertEqual(plans, [])
        self.assertEqual(engine.diagnostics.get("retest_order_block_below_two_x"), 1)
        self.assertIs(
            engine.setups[0].state,
            TrendlineRetestState.WAITING_CONFIRMATION,
        )
        # Once price leaves the first-retest episode without confirmation, a
        # prettier OB days later cannot retroactively become the same trade.
        engine.on_bar(self.bar(10, 10.3, 10.9, 10.2, 10.8))
        self.assertIs(
            engine.setups[0].state,
            TrendlineRetestState.MISSED_WITHOUT_CONFIRMATION,
        )


if __name__ == "__main__":
    unittest.main()
