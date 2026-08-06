from __future__ import annotations

import unittest

from nautilus_trader.model.enums import TriggerType

from c10_flow_parent_execution import PROTECTIVE_STOP_TRIGGER_TYPE
from c10_flow_parent_execution import protective_action


class ParentProtectedExecutionTests(unittest.TestCase):
    def test_stop_uses_last_trade_trigger(self) -> None:
        self.assertEqual(PROTECTIVE_STOP_TRIGGER_TYPE, TriggerType.LAST_PRICE)

    def test_long_fill_already_below_stop_exits_at_market(self) -> None:
        self.assertEqual(
            protective_action(
                direction=1,
                last_price=99.0,
                stop_price=99.5,
                target_price=105.0,
            ),
            "MARKET_STOP",
        )

    def test_short_fill_already_above_stop_exits_at_market(self) -> None:
        self.assertEqual(
            protective_action(
                direction=-1,
                last_price=101.0,
                stop_price=100.5,
                target_price=95.0,
            ),
            "MARKET_STOP",
        )

    def test_target_gap_exits_at_market(self) -> None:
        self.assertEqual(
            protective_action(
                direction=1,
                last_price=106.0,
                stop_price=99.0,
                target_price=105.0,
            ),
            "MARKET_TARGET",
        )

    def test_normal_fill_receives_resting_stop_and_target(self) -> None:
        self.assertEqual(
            protective_action(
                direction=-1,
                last_price=100.0,
                stop_price=102.0,
                target_price=95.0,
            ),
            "RESTING_STOP_TARGET",
        )


if __name__ == "__main__":
    unittest.main()
