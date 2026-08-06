from __future__ import annotations

import unittest

from nautilus_trader.model.enums import TriggerType

from c10_flow_emulated_stop import PROTECTIVE_STOP_EMULATION_TRIGGER_TYPE
from c10_flow_emulated_stop import PROTECTIVE_STOP_TRIGGER_TYPE


class EmulatedStopControlTests(unittest.TestCase):
    def test_structural_stop_and_emulator_both_use_last_trade(self) -> None:
        self.assertEqual(PROTECTIVE_STOP_TRIGGER_TYPE, TriggerType.LAST_PRICE)
        self.assertEqual(
            PROTECTIVE_STOP_EMULATION_TRIGGER_TYPE,
            TriggerType.LAST_PRICE,
        )


if __name__ == "__main__":
    unittest.main()
