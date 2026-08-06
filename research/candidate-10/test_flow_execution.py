from __future__ import annotations

import unittest

from nautilus_trader.model.enums import TriggerType

from c10_flow_strategy import FLOW_STOP_TRIGGER_TYPE


class FlowExecutionControlTests(unittest.TestCase):
    def test_protective_stop_uses_last_trade_trigger(self) -> None:
        self.assertEqual(FLOW_STOP_TRIGGER_TYPE, TriggerType.LAST_PRICE)


if __name__ == "__main__":
    unittest.main()
