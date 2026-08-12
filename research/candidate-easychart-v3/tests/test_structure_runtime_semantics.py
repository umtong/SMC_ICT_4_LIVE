from __future__ import annotations

from types import SimpleNamespace
import unittest

import structure_runtime_v3  # noqa: F401
from contracts_v5 import Pivot
from domain import Candle, Side
from scenario_execution_v5 import ScenarioExecutionMixin
from structure_v5 import CausalStructureBook


class StructureRuntimeSemanticsTest(unittest.TestCase):
    @staticmethod
    def bar(ts: int, *, high: float, low: float) -> Candle:
        close = (high + low) / 2.0
        return Candle(ts_close_ns=ts, open=close, high=high, low=low, close=close, volume=1.0)

    def test_equal_high_touch_does_not_spend_liquidity(self) -> None:
        book = CausalStructureBook("TEST", 15, 0.1, pivot_spans=(2,))
        pivot = Pivot(
            pivot_id="high",
            side="HIGH",
            price=100.0,
            index=0,
            event_time_ns=1,
            observed_index=0,
            observed_time_ns=10,
            span=2,
            strength_ratio=1.0,
        )
        book.pivots.append(pivot)
        book._active_pivots[pivot.pivot_id] = pivot
        book.observe_price(self.bar(20, high=100.0, low=99.0))
        self.assertIsNotNone(pivot.first_touch_time_ns)
        self.assertFalse(pivot.consumed)
        self.assertIn(pivot.pivot_id, book._active_pivots)

    def test_one_tick_trade_beyond_high_spends_liquidity(self) -> None:
        book = CausalStructureBook("TEST", 15, 0.1, pivot_spans=(2,))
        pivot = Pivot(
            pivot_id="high",
            side="HIGH",
            price=100.0,
            index=0,
            event_time_ns=1,
            observed_index=0,
            observed_time_ns=10,
            span=2,
            strength_ratio=1.0,
        )
        book.pivots.append(pivot)
        book._active_pivots[pivot.pivot_id] = pivot
        book.observe_price(self.bar(20, high=100.1, low=99.0))
        self.assertTrue(pivot.consumed)
        self.assertNotIn(pivot.pivot_id, book._active_pivots)

    def test_channel_acceptance_uses_prebreak_origin_not_one_tick_edge(self) -> None:
        origin = SimpleNamespace(price=95.0)
        setup = SimpleNamespace(side=Side.LONG, acceptance_origin=origin)
        engine = SimpleNamespace(tick_size=0.1)
        stop = ScenarioExecutionMixin._acceptance_stop(engine, setup, 123)
        self.assertAlmostEqual(stop, 94.9)


if __name__ == "__main__":
    unittest.main()
