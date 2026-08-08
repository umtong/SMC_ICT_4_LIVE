from __future__ import annotations

from types import SimpleNamespace
import unittest

from internal_reclaim import (
    MINUTE_NS,
    SOURCE,
    InternalReclaimEngine,
    _construct_trade_plan,
    is_internal_reclaim_plan,
)
from logic import Direction, Scenario


class InternalReclaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SimpleNamespace(
            effective_maker_rate=0.0004,
            effective_taker_rate=0.0008,
            min_net_r=1.25,
        )

    @staticmethod
    def bar(index: int, price: float = 100.0):
        return SimpleNamespace(
            ts_ns=(index + 1) * MINUTE_NS,
            open=price,
            high=price + 0.2,
            low=price - 0.2,
            close=price + 0.05,
            volume=100.0,
            taker_buy_volume=55.0,
        )

    def test_rejects_non_monotonic_completed_bars(self) -> None:
        engine = InternalReclaimEngine(self.config, "BTCUSDT-PERP.BINANCE")
        engine.on_bar(self.bar(0))
        with self.assertRaises(ValueError):
            engine.on_bar(self.bar(0))

    def test_prior_hour_boundary_is_visible_only_after_rollover(self) -> None:
        engine = InternalReclaimEngine(self.config, "BTCUSDT-PERP.BINANCE")
        for index in range(59):
            engine.on_bar(self.bar(index, 100.0 + index * 0.01))
        self.assertEqual([], engine.targets)
        engine.on_bar(self.bar(59, 100.6))
        self.assertEqual([], engine.targets)
        engine.on_bar(self.bar(60, 100.7))
        hourly = [target for target in engine.targets if target.source == "PRIOR_1H_AUCTION"]
        self.assertEqual(2, len(hourly))
        self.assertTrue(all(target.created_ts_ns == 61 * MINUTE_NS for target in hourly))

    def test_project_trade_plan_adapter_preserves_economic_fields(self) -> None:
        details = {"source": SOURCE, "sweep_ts_ns": 10 * MINUTE_NS}
        plan = _construct_trade_plan({
            "scenario_id": "TEST-IRX-1",
            "scenario": Scenario.FAR,
            "direction": Direction.LONG,
            "observed_ts_ns": 20 * MINUTE_NS,
            "expected_entry": 100.0,
            "stop_price": 99.0,
            "target_price": 103.0,
            "loss_per_unit": 1.2,
            "net_r": 2.0,
            "expire_ts_ns": 28 * MINUTE_NS,
            "details": details,
        })
        self.assertTrue(is_internal_reclaim_plan(plan))
        self.assertEqual("TEST-IRX-1", plan.scenario_id)
        self.assertEqual(Direction.LONG, plan.direction)
        self.assertEqual(Scenario.FAR, plan.scenario)
        self.assertGreater(plan.target_price, plan.expected_entry)
        self.assertLess(plan.stop_price, plan.expected_entry)

    def test_non_internal_plan_is_not_claimed(self) -> None:
        plan = SimpleNamespace(details={"source": "SCDAM"})
        self.assertFalse(is_internal_reclaim_plan(plan))


if __name__ == "__main__":
    unittest.main()
