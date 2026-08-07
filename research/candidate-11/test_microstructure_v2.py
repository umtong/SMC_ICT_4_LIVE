from __future__ import annotations

import unittest

from microstructure import SECOND_NS, FlowBar
from microstructure_v2 import (
    SOURCE,
    CombinedMicrostructureEngine,
    VWAPExhaustionEngine,
    _Exhaustion,
)


class MicrostructureV2Tests(unittest.TestCase):
    @staticmethod
    def bar(index: int, open_price: float, close_price: float) -> FlowBar:
        price = (open_price + close_price) / 2
        return FlowBar(
            ts_ns=(index + 1) * SECOND_NS,
            open=open_price,
            high=max(open_price, close_price) + 0.05,
            low=min(open_price, close_price) - 0.05,
            close=close_price,
            volume=10.0,
            buy_volume=5.0,
            sell_volume=5.0,
            quote_notional=price * 10.0,
            signed_notional=0.0,
            trade_count=10,
            max_trade_notional=price,
        )

    def test_vwap_plan_is_costed_and_points_to_frozen_value(self) -> None:
        engine = VWAPExhaustionEngine("BTCUSDT-PERP.BINANCE")
        active = _Exhaustion(
            impulse_direction="LONG",
            reversal_direction="SHORT",
            detected_ts_ns=10 * SECOND_NS,
            frozen_vwap=98.0,
            extreme=101.0,
            deviation_score=1.5,
            flow_score=2.5,
            impact_decay=0.3,
        )
        bar = self.bar(20, 100.0, 99.5)
        plan = engine._costed_plan(bar, active, atr=1.0, move_score=1.0, flow_score=1.0)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual("SHORT", plan.direction)
        self.assertEqual(98.0, plan.target_price)
        self.assertGreater(plan.stop_price, plan.expected_entry)
        self.assertGreaterEqual(plan.net_r, 1.25)
        self.assertEqual(SOURCE, plan.details["source"])

    def test_wrong_side_frozen_value_is_rejected(self) -> None:
        engine = VWAPExhaustionEngine("BTCUSDT-PERP.BINANCE")
        active = _Exhaustion(
            impulse_direction="LONG",
            reversal_direction="SHORT",
            detected_ts_ns=10 * SECOND_NS,
            frozen_vwap=102.0,
            extreme=101.0,
            deviation_score=1.5,
            flow_score=2.5,
            impact_decay=0.3,
        )
        plan = engine._costed_plan(self.bar(20, 100.0, 99.5), active, atr=1.0, move_score=1.0, flow_score=1.0)
        self.assertIsNone(plan)
        self.assertEqual(1, engine.skips["EXHAUSTION_NON_CAUSAL_PRICE_ORDER"])

    def test_combined_engine_merges_skip_reasons(self) -> None:
        engine = CombinedMicrostructureEngine("BTCUSDT-PERP.BINANCE")
        engine.pool.skips["POOL"] += 2
        engine.exhaustion.skips["EXHAUSTION"] += 3
        self.assertEqual(2, engine.skips["POOL"])
        self.assertEqual(3, engine.skips["EXHAUSTION"])


if __name__ == "__main__":
    unittest.main()
