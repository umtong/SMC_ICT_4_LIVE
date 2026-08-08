from __future__ import annotations

import unittest

from microstructure import SECOND_NS, FlowBar
from microstructure_v3 import BalanceAcceptanceEngine, _Acceptance, _Balance


class BalanceAcceptanceTargetAvailabilityTests(unittest.TestCase):
    def test_confirmation_bar_cannot_consume_target_before_passive_entry(self) -> None:
        engine = BalanceAcceptanceEngine("BTCUSDT-PERP.BINANCE")
        balance = _Balance(
            start_ts_ns=1,
            completed_ts_ns=300 * SECOND_NS,
            open=99.0,
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
            breakout_extreme=101.0,
            breakout_move_score=1.5,
            breakout_flow_score=2.0,
            breakout_impact_efficiency=0.75,
            phase="WAIT_REACCELERATION",
            retest_extreme=99.7,
            retest_ts_ns=315 * SECOND_NS,
        )
        bar = FlowBar(
            ts_ns=321 * SECOND_NS,
            open=101.2,
            high=102.1,
            low=101.1,
            close=101.8,
            volume=10.0,
            buy_volume=8.0,
            sell_volume=2.0,
            quote_notional=1015.0,
            signed_notional=600.0,
            trade_count=10,
            max_trade_notional=150.0,
        )
        plan = engine._costed_plan(
            bar,
            event,
            atr=1.0,
            move_score=1.0,
            flow_score=1.0,
        )
        self.assertIsNone(plan)
        self.assertEqual(1, engine.skips["BALANCE_TARGET_CONSUMED_BEFORE_ENTRY"])


if __name__ == "__main__":
    unittest.main()
