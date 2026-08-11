from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from domain_v3 import Candle, Side
from market_v4 import StructuralPivot
from market_v11 import (
    ImpulseConfluenceContext,
    TrendlineImpulseContextEngine,
    WickTrendline,
    evaluate_session_impulse_confluence,
)


NS = 60_000_000_000


def pivot(index, side, level, observed=None):
    observed = index if observed is None else observed
    return StructuralPivot(index, observed, side, level, (index + 1) * NS, (observed + 1) * NS)


def candle(index, open_, high, low, close):
    return Candle(index * NS, (index + 1) * NS - 1, open_, high, low, close, 1.0)


def long_context():
    h1 = pivot(0, "HIGH", 110.0)
    h2 = pivot(2, "HIGH", 106.0)
    line = WickTrendline(Side.LONG, h1, h2, (106.0 - 110.0) / (h2.event_time_ns - h1.event_time_ns))
    return ImpulseConfluenceContext(
        context_id="ctx",
        side=Side.LONG,
        observed_time_ns=8 * NS,
        break_time_ns=4 * NS,
        origin=pivot(1, "LOW", 90.0),
        terminal=pivot(6, "HIGH", 120.0, 7),
        line=line,
    )


class TestImpulseConfluence(unittest.TestCase):
    def test_fib_and_broken_line_have_distinct_roles(self):
        context = long_context()
        fib = context.fib_0618
        line = context.trendline_price(10 * NS)
        extreme = min(fib, line) - 1.0
        close = max(fib, line) + 1.0
        result = evaluate_session_impulse_confluence(
            side=Side.LONG,
            context=context,
            observed_time_ns=10 * NS,
            reclaim_close=close,
            sweep_extreme=extreme,
            session_boundary=95.0,
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.entry, max(95.0, fib, line))
        self.assertEqual(result.target, 120.0)

    def test_missing_fib_interaction_is_rejected_without_tolerance(self):
        context = long_context()
        result = evaluate_session_impulse_confluence(
            side=Side.LONG,
            context=context,
            observed_time_ns=10 * NS,
            reclaim_close=110.0,
            sweep_extreme=context.fib_0618 + 0.1,
            session_boundary=100.0,
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "FIB_NOT_SWEPT_AND_RECLAIMED")

    def test_short_geometry_is_symmetric(self):
        l1 = pivot(0, "LOW", 90.0)
        l2 = pivot(2, "LOW", 94.0)
        line = WickTrendline(Side.SHORT, l1, l2, (94.0 - 90.0) / (l2.event_time_ns - l1.event_time_ns))
        context = ImpulseConfluenceContext(
            context_id="short",
            side=Side.SHORT,
            observed_time_ns=8 * NS,
            break_time_ns=4 * NS,
            origin=pivot(1, "HIGH", 120.0),
            terminal=pivot(6, "LOW", 90.0, 7),
            line=line,
        )
        fib = context.fib_0618
        projected = context.trendline_price(10 * NS)
        result = evaluate_session_impulse_confluence(
            side=Side.SHORT,
            context=context,
            observed_time_ns=10 * NS,
            reclaim_close=min(fib, projected) - 1.0,
            sweep_extreme=max(fib, projected) + 1.0,
            session_boundary=110.0,
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.entry, min(110.0, fib, projected))
        self.assertEqual(result.target, 90.0)


class TestContextEngine(unittest.TestCase):
    def test_pivot_is_not_backdated_and_complete_impulse_required(self):
        engine = TrendlineImpulseContextEngine("BTCUSDT")
        engine.on_pivot(pivot(0, "HIGH", 110.0))
        engine.on_pivot(pivot(1, "LOW", 90.0))
        engine.on_pivot(pivot(2, "HIGH", 106.0))
        engine.on_close(candle(3, 104.0, 109.0, 103.0, 108.0))
        self.assertIn(Side.LONG, engine.pending)
        self.assertEqual(engine.contexts, [])
        terminal = pivot(5, "HIGH", 120.0, 7)
        engine.on_pivot(terminal)
        self.assertEqual(len(engine.contexts), 1)
        self.assertEqual(engine.contexts[0].observed_time_ns, terminal.observed_time_ns)
        self.assertEqual(engine.contexts[0].fib_0618, 120.0 - 0.618 * 30.0)


if __name__ == "__main__":
    unittest.main()
