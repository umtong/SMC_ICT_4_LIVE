from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))

from logic import Auction, BarObs, CausalAuctionEngine, Direction, LogicConfig, Pool, RiskSizer, Scenario, Side, TradePlan


def bar(i: int, o: float, h: float, l: float, c: float, flow: float = 0.0, volume: float = 100.0) -> BarObs:
    buy = volume * (flow + 1.0) / 2.0
    return BarObs(i * 60_000_000_000, o, h, l, c, volume, buy)


class CausalityTests(unittest.TestCase):
    def test_pivot_not_known_at_visual_pivot(self) -> None:
        cfg = LogicConfig(atr_period=2, volume_period=2, pivot_wing=1)
        engine = CausalAuctionEngine(cfg, "BTCUSDT.BINANCE")
        engine.on_bar(bar(1, 10, 11, 9, 10))
        engine.on_bar(bar(2, 10, 13, 9, 11))
        self.assertFalse(any(p.source == "CAUSAL_PIVOT_HIGH" for p in engine.pools))
        engine.on_bar(bar(3, 11, 12, 10, 11))
        pool = next(p for p in engine.pools if p.source == "CAUSAL_PIVOT_HIGH")
        self.assertEqual(pool.candidate_ts_ns, 2 * 60_000_000_000)
        self.assertEqual(pool.confirmed_ts_ns, 3 * 60_000_000_000)
        self.assertGreater(pool.confirmed_ts_ns, pool.candidate_ts_ns)

    def test_far_requires_confirmation_before_retrace(self) -> None:
        # The test isolates scenario ordering. Its synthetic sweep has an
        # intentionally broad stop, so the unrelated production stop-bound
        # gate is widened only for this fixture.
        cfg = LogicConfig(
            atr_period=2,
            volume_period=2,
            pivot_wing=1,
            min_net_r=0.1,
            max_stop_atr=3.0,
        )
        engine = CausalAuctionEngine(cfg, "BTCUSDT.BINANCE")
        pool = Pool("p", Side.HIGH, 100.0, "TEST", 1, 2, 0, 100)
        engine.pools.extend([pool, Pool("target", Side.LOW, 90.0, "TEST", 1, 2, 0, 100)])
        engine.bars.extend([bar(1, 99, 100, 98, 99), bar(2, 99, 101, 98, 100)])
        engine._index = 1
        engine.true_ranges.extend([2.0, 2.0])
        engine.volumes.extend([100.0, 100.0])
        engine.active = Auction(pool, bar(2, 99, 101, 98, 99, 0.3), 1, 2.0, 98.0, 101.0, True, False)
        self.assertIsNone(engine._try_far(engine.active, bar(3, 99, 100, 98.5, 99, -0.2)))
        self.assertEqual(engine.active.state, "COMPETE")
        self.assertIsNone(engine._try_far(engine.active, bar(4, 99, 99, 96.5, 97, -0.5)))
        self.assertEqual(engine.active.state, "RETRACE")
        plan = engine._try_far(engine.active, bar(5, 97, 98, 96, 97.5, -0.1))
        self.assertIsNotNone(plan)
        self.assertEqual(plan.scenario, Scenario.FAR)
        self.assertEqual(plan.direction, Direction.SHORT)

    def test_global_slot_is_a_hard_invariant(self) -> None:
        cfg = LogicConfig()
        engine = CausalAuctionEngine(cfg, "BTCUSDT.BINANCE")
        pool = Pool("p", Side.LOW, 100.0, "TEST", 1, 2, 0, 100)
        engine.active = Auction(pool, bar(1, 100, 101, 99, 100), 0, 2.0, 101.0, 99.0, True, False, state="CONFIRMED")
        plan = TradePlan("p", Scenario.FAR, Direction.LONG, 1, 100.0, 98.0, 105.0, 2.0, 2.2, 4.8, 2.18, "TEST")
        engine.mark_submitted(plan, Decimal("1"))
        with self.assertRaises(RuntimeError):
            engine.mark_submitted(plan, Decimal("1"))


class RiskTests(unittest.TestCase):
    def test_exact_three_percent_budget_and_increment_floor(self) -> None:
        decision = RiskSizer(0.03).size(
            nav=Decimal("100000"), loss_per_unit=Decimal("100"), entry_price=Decimal("50000"),
            quantity_increment=Decimal("0.001"), min_quantity=Decimal("0.001"),
            min_notional=Decimal("5"), margin_init=Decimal("0.05"), free_balance=Decimal("100000"),
        )
        self.assertTrue(decision.feasible)
        self.assertEqual(decision.planned_loss_budget, Decimal("3000.00"))
        self.assertEqual(decision.quantity, Decimal("30.00"))
        self.assertLessEqual(decision.expected_total_loss, decision.planned_loss_budget)

    def test_margin_infeasibility_rejects_instead_of_clipping(self) -> None:
        decision = RiskSizer(0.03).size(
            nav=Decimal("100000"), loss_per_unit=Decimal("10"), entry_price=Decimal("50000"),
            quantity_increment=Decimal("0.001"), min_quantity=Decimal("0.001"),
            min_notional=Decimal("5"), margin_init=Decimal("0.05"), free_balance=Decimal("1000"),
        )
        self.assertFalse(decision.feasible)
        self.assertEqual(decision.reason, "ACTUAL_MARGIN_INFEASIBLE")
        self.assertEqual(decision.quantity, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
