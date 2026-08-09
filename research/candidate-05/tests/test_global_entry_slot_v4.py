from __future__ import annotations

import unittest

from global_entry_slot_v4 import FinalSharedAccountEntryCoordinator


class FinalSharedAccountEntryCoordinatorTest(unittest.TestCase):
    def test_open_position_cannot_be_released_without_close_event(self) -> None:
        slot = FinalSharedAccountEntryCoordinator()
        self.assertTrue(
            slot.acquire_entry_intent(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=1,
                reason="ENTRY",
            ),
        )
        self.assertTrue(
            slot.position_opened(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=2,
                reason="FILL",
            ),
        )
        self.assertFalse(
            slot.release(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=3,
                reason="ILLEGAL_FORCE_RELEASE",
            ),
        )
        self.assertTrue(
            slot.position_closed(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=4,
                reason="CLOSE",
            ),
        )
        self.assertTrue(
            slot.release(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=5,
                reason="LEGAL_RELEASE",
            ),
        )
        audit = slot.audit()
        self.assertFalse(audit["audit_pass"])
        self.assertEqual(audit["release_phase_mismatches"], 1)

    def test_cancelled_entry_intent_can_release_directly(self) -> None:
        slot = FinalSharedAccountEntryCoordinator()
        self.assertTrue(
            slot.acquire_entry_intent(
                owner="ETHUSDT-PERP.BINANCE",
                ts_event=1,
                reason="ENTRY",
            ),
        )
        self.assertTrue(
            slot.release(
                owner="ETHUSDT-PERP.BINANCE",
                ts_event=2,
                reason="CANCELLED_UNFILLED",
            ),
        )
        self.assertTrue(slot.audit()["audit_pass"])

    def test_closed_position_can_release(self) -> None:
        slot = FinalSharedAccountEntryCoordinator()
        self.assertTrue(slot.acquire_entry_intent(owner="SOLUSDT-PERP.BINANCE", ts_event=1, reason="ENTRY"))
        self.assertTrue(slot.position_opened(owner="SOLUSDT-PERP.BINANCE", ts_event=2, reason="FILL"))
        self.assertTrue(slot.position_closed(owner="SOLUSDT-PERP.BINANCE", ts_event=3, reason="CLOSE"))
        self.assertTrue(slot.release(owner="SOLUSDT-PERP.BINANCE", ts_event=4, reason="RELEASE"))
        audit = slot.audit()
        self.assertTrue(audit["audit_pass"])
        self.assertEqual(audit["max_entry_intents_plus_positions_replayed"], 1)


if __name__ == "__main__":
    unittest.main()
