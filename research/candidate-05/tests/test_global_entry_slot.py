from __future__ import annotations

import unittest

from global_entry_slot_v2 import StrictGlobalEntrySlotCoordinator


class StrictGlobalEntrySlotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.slot = StrictGlobalEntrySlotCoordinator()

    def test_acquire_conflict_release_replays_one_owner(self) -> None:
        self.assertTrue(
            self.slot.acquire(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=1,
                reason="BTC_ENTRY",
            ),
        )
        self.assertFalse(
            self.slot.acquire(
                owner="ETHUSDT-PERP.BINANCE",
                ts_event=2,
                reason="ETH_ENTRY",
            ),
        )
        self.assertEqual(self.slot.owner, "BTCUSDT-PERP.BINANCE")
        self.assertTrue(
            self.slot.release(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=3,
                reason="BTC_CLOSED",
            ),
        )
        audit = self.slot.audit()
        self.assertTrue(audit["audit_pass"])
        self.assertEqual(audit["max_simultaneous_owners_replayed"], 1)
        self.assertEqual(audit["conflicts"], 1)
        self.assertEqual(audit["release_mismatches"], 0)
        self.assertEqual(audit["active_owners_at_end"], [])

    def test_same_owner_reentry_does_not_create_second_owner(self) -> None:
        self.assertTrue(
            self.slot.acquire(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=1,
                reason="ENTRY",
            ),
        )
        self.assertTrue(
            self.slot.acquire(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=2,
                reason="IDEMPOTENT_REENTRY",
            ),
        )
        self.assertTrue(
            self.slot.release(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=3,
                reason="CLOSED",
            ),
        )
        audit = self.slot.audit()
        self.assertTrue(audit["audit_pass"])
        self.assertEqual(audit["max_simultaneous_owners_replayed"], 1)
        self.assertEqual(audit["acquisitions"], 1)
        self.assertEqual(audit["releases"], 1)

    def test_wrong_owner_release_is_an_audit_failure(self) -> None:
        self.assertTrue(
            self.slot.acquire(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=1,
                reason="ENTRY",
            ),
        )
        self.assertFalse(
            self.slot.release(
                owner="ETHUSDT-PERP.BINANCE",
                ts_event=2,
                reason="WRONG_RELEASE",
            ),
        )
        self.assertTrue(
            self.slot.release(
                owner="BTCUSDT-PERP.BINANCE",
                ts_event=3,
                reason="RIGHT_RELEASE",
            ),
        )
        audit = self.slot.audit()
        self.assertFalse(audit["audit_pass"])
        self.assertEqual(audit["release_mismatches"], 1)

    def test_unreleased_owner_at_end_is_an_audit_failure(self) -> None:
        self.assertTrue(
            self.slot.acquire(
                owner="SOLUSDT-PERP.BINANCE",
                ts_event=1,
                reason="ENTRY",
            ),
        )
        audit = self.slot.audit()
        self.assertFalse(audit["audit_pass"])
        self.assertEqual(
            audit["active_owners_at_end"],
            ["SOLUSDT-PERP.BINANCE"],
        )

    def test_reset_clears_owner_events_and_sequence(self) -> None:
        self.assertTrue(
            self.slot.acquire(
                owner="XRPUSDT-PERP.BINANCE",
                ts_event=1,
                reason="ENTRY",
            ),
        )
        self.slot.reset()
        self.assertIsNone(self.slot.owner)
        self.assertEqual(self.slot.events(), [])
        audit = self.slot.audit()
        self.assertTrue(audit["audit_pass"])
        self.assertEqual(audit["events"], 0)


if __name__ == "__main__":
    unittest.main()
