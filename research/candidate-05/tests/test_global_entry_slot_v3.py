from __future__ import annotations

import unittest

from global_entry_slot_v3 import ENTRY_INTENT
from global_entry_slot_v3 import POSITION_OPEN
from global_entry_slot_v3 import SharedAccountEntryCoordinator


class SharedAccountEntryCoordinatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.slot = SharedAccountEntryCoordinator()

    def test_pending_to_position_to_idle_never_exceeds_one(self) -> None:
        self.assertTrue(
            self.slot.acquire_entry_intent(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=1,
                reason="ENTRY",
            ),
        )
        self.assertEqual(self.slot.phase, ENTRY_INTENT)
        self.assertTrue(
            self.slot.position_opened(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=2,
                reason="FILL",
            ),
        )
        self.assertEqual(self.slot.phase, POSITION_OPEN)
        self.assertFalse(
            self.slot.acquire_entry_intent(
                owner="ETHUSDT-PERP.BINANCE",
                ts_event=3,
                reason="CONFLICT",
            ),
        )
        self.assertTrue(
            self.slot.position_closed(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=4,
                reason="EXIT",
            ),
        )
        self.assertTrue(
            self.slot.release(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=5,
                reason="CLOSED_RELEASE",
            ),
        )
        audit = self.slot.audit()
        self.assertTrue(audit["audit_pass"])
        self.assertEqual(audit["max_unfilled_entry_intents_replayed"], 1)
        self.assertEqual(audit["max_open_positions_replayed"], 1)
        self.assertEqual(audit["max_entry_intents_plus_positions_replayed"], 1)
        self.assertEqual(audit["conflicts"], 1)
        self.assertTrue(audit["idle_at_end"])

    def test_unfilled_entry_can_release_without_position(self) -> None:
        self.assertTrue(
            self.slot.acquire_entry_intent(
                owner="SOLUSDT-PERP.BINANCE",
                ts_event=1,
                reason="ENTRY",
            ),
        )
        self.assertTrue(
            self.slot.release(
                owner="SOLUSDT-PERP.BINANCE",
                ts_event=2,
                reason="CANCELLED_UNFILLED",
            ),
        )
        audit = self.slot.audit()
        self.assertTrue(audit["audit_pass"])
        self.assertEqual(audit["positions_opened"], 0)
        self.assertEqual(audit["max_entry_intents_plus_positions_replayed"], 1)

    def test_second_entry_by_same_owner_while_position_open_is_rejected(self) -> None:
        self.assertTrue(
            self.slot.acquire_entry_intent(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=1,
                reason="ENTRY",
            ),
        )
        self.assertTrue(
            self.slot.position_opened(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=2,
                reason="FILL",
            ),
        )
        self.assertFalse(
            self.slot.acquire_entry_intent(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=3,
                reason="ILLEGAL_SECOND_ENTRY",
            ),
        )
        self.assertTrue(
            self.slot.position_closed(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=4,
                reason="EXIT",
            ),
        )
        self.assertTrue(
            self.slot.release(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=5,
                reason="RELEASE",
            ),
        )
        audit = self.slot.audit()
        self.assertTrue(audit["audit_pass"])
        self.assertEqual(audit["conflicts"], 1)

    def test_position_open_without_entry_intent_fails_audit(self) -> None:
        self.assertFalse(
            self.slot.position_opened(
                owner="XRPUSDT-PERP.BINANCE",
                ts_event=1,
                reason="UNAUTHORIZED_FILL",
            ),
        )
        audit = self.slot.audit()
        self.assertFalse(audit["audit_pass"])
        self.assertEqual(audit["mismatches"], 1)

    def test_position_close_by_wrong_owner_fails_audit(self) -> None:
        self.assertTrue(
            self.slot.acquire_entry_intent(
                owner="ETHUSDT-PERP.BINANCE",
                ts_event=1,
                reason="ENTRY",
            ),
        )
        self.assertTrue(
            self.slot.position_opened(
                owner="ETHUSDT-PERP.BINANCE",
                ts_event=2,
                reason="FILL",
            ),
        )
        self.assertFalse(
            self.slot.position_closed(
                owner="SOLUSDT-PERP.BINANCE",
                ts_event=3,
                reason="WRONG_CLOSE",
            ),
        )
        self.assertTrue(
            self.slot.position_closed(
                owner="ETHUSDT-PERP.BINANCE",
                ts_event=4,
                reason="RIGHT_CLOSE",
            ),
        )
        self.assertTrue(
            self.slot.release(
                owner="ETHUSDT-PERP.BINANCE",
                ts_event=5,
                reason="RELEASE",
            ),
        )
        audit = self.slot.audit()
        self.assertFalse(audit["audit_pass"])
        self.assertEqual(audit["mismatches"], 1)

    def test_active_state_at_end_fails_audit(self) -> None:
        self.assertTrue(
            self.slot.acquire_entry_intent(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=1,
                reason="ENTRY",
            ),
        )
        audit = self.slot.audit()
        self.assertFalse(audit["audit_pass"])
        self.assertFalse(audit["idle_at_end"])
        self.assertEqual(audit["owner_at_end"], "BTCUSDT-PERP.BINANCE")


if __name__ == "__main__":
    unittest.main()
