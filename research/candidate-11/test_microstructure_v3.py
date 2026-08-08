from __future__ import annotations

import unittest

from microstructure import SECOND_NS, FlowBar
from microstructure_v3 import (
    SOURCE,
    BalanceAcceptanceEngine,
    CombinedMicrostructureV3Engine,
    _Acceptance,
    _Balance,
)


class MicrostructureV3Tests(unittest.TestCase):
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

    def test_measured_move_target_is_frozen_balance_extension(self) -> None:
        engine = BalanceAcceptanceEngine("BTCUSDT-PERP.BINANCE")
        balance = _Balance(
            start_ts_ns=1,
            completed_ts_ns=300 * SECOND_NS,
            open=99.5,
            high=100.0,
            low=98.0,
            close=99.0,
            vwap=99.0,
            range=2.0,
            path_efficiency=0.2,
            consumed=True,
        )
        event = _Acceptance(
            balance=balance,
            direction="LONG",
            boundary=100.0,
            breakout_ts_ns=310 * SECOND_NS,
            breakout_extreme=100.8,
            breakout_move_score=1.5,
            breakout_flow_score=2.0,
            breakout_impact_efficiency=0.75,
            phase="WAIT_REACCELERATION",
            retest_extreme=99.7,
            retest_ts_ns=315 * SECOND_NS,
        )
        plan = engine._costed_plan(
            self.bar(320, 100.3, 100.5),
            event,
            atr=1.0,
            move_score=1.0,
            flow_score=1.0,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(102.0, plan.target_price)
        self.assertEqual("LONG", plan.direction)
        self.assertLess(plan.stop_price, plan.expected_entry)
        self.assertGreaterEqual(plan.net_r, 1.25)
        self.assertEqual(SOURCE, plan.details["source"])

    def test_wrong_side_retest_is_rejected(self) -> None:
        engine = BalanceAcceptanceEngine("BTCUSDT-PERP.BINANCE")
        balance = _Balance(
            start_ts_ns=1,
            completed_ts_ns=300 * SECOND_NS,
            open=99.5,
            high=100.0,
            low=99.0,
            close=99.5,
            vwap=99.5,
            range=1.0,
            path_efficiency=0.2,
            consumed=True,
        )
        event = _Acceptance(
            balance=balance,
            direction="LONG",
            boundary=100.0,
            breakout_ts_ns=310 * SECOND_NS,
            breakout_extreme=100.8,
            breakout_move_score=1.5,
            breakout_flow_score=2.0,
            breakout_impact_efficiency=0.75,
            phase="WAIT_REACCELERATION",
            retest_extreme=100.5,
            retest_ts_ns=315 * SECOND_NS,
        )
        plan = engine._costed_plan(
            self.bar(320, 100.3, 100.5),
            event,
            atr=1.0,
            move_score=1.0,
            flow_score=1.0,
        )
        self.assertIsNone(plan)
        self.assertGreater(
            engine.skips["BALANCE_NON_CAUSAL_PRICE_ORDER"]
            + engine.skips["BALANCE_STOP_GEOMETRY"],
            0,
        )

    def test_combined_engine_merges_skip_reasons(self) -> None:
        engine = CombinedMicrostructureV3Engine("BTCUSDT-PERP.BINANCE")
        engine.existing.pool.skips["POOL"] += 2
        engine.existing.exhaustion.skips["EXHAUSTION"] += 3
        engine.balance.skips["BALANCE"] += 4
        self.assertEqual(2, engine.skips["POOL"])
        self.assertEqual(3, engine.skips["EXHAUSTION"])
        self.assertEqual(4, engine.skips["BALANCE"])


if __name__ == "__main__":
    unittest.main()
