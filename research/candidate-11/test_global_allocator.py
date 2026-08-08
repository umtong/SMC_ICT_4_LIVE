from decimal import Decimal
import unittest

from global_allocator import Candidate, GlobalCandidateMutex, SlotState


def candidate(symbol: str, scenario: str, ts: int, r: str, error: str = "0.10", loss: str = "100") -> Candidate:
    return Candidate(symbol, scenario, ts, Decimal(r), Decimal("50000"), Decimal(loss), Decimal(error))


class GlobalAllocatorTests(unittest.TestCase):
    def test_same_timestamp_is_not_decided_by_subscription_order(self) -> None:
        mutex = GlobalCandidateMutex()
        lower = candidate("BTCUSDT", "btc", 100, "1.5", "0.08")
        higher = candidate("ETHUSDT", "eth", 100, "2.2", "0.08")
        self.assertIsNone(mutex.add(lower))
        self.assertIsNone(mutex.add(higher))
        result = mutex.flush()
        self.assertEqual(result.winner, higher)
        self.assertEqual(result.rejected[0][0], lower)

    def test_lower_error_bound_precedes_higher_r(self) -> None:
        mutex = GlobalCandidateMutex()
        safer = candidate("SOLUSDT", "safe", 100, "1.4", "0.02")
        richer = candidate("XRPUSDT", "rich", 100, "3.0", "0.10")
        mutex.add(richer)
        mutex.add(safer)
        self.assertEqual(mutex.flush().winner, safer)

    def test_pending_entry_occupies_global_slot(self) -> None:
        mutex = GlobalCandidateMutex()
        first = candidate("BTCUSDT", "one", 100, "2")
        mutex.mark_entry_submitted(first)
        mutex.add(candidate("ETHUSDT", "two", 101, "3"))
        result = mutex.flush()
        self.assertIsNone(result.winner)
        self.assertEqual(result.rejected[0][1], "GLOBAL_SLOT_OCCUPIED")
        self.assertEqual(mutex.state, SlotState.ENTRY_PENDING)

    def test_lifecycle_releases_only_matching_scenario(self) -> None:
        mutex = GlobalCandidateMutex()
        first = candidate("BTCUSDT", "one", 100, "2")
        mutex.mark_entry_submitted(first)
        with self.assertRaises(RuntimeError):
            mutex.mark_entry_filled("wrong")
        mutex.mark_entry_filled("one")
        with self.assertRaises(RuntimeError):
            mutex.mark_position_closed("wrong")
        mutex.mark_position_closed("one")
        self.assertEqual(mutex.state, SlotState.FREE)


if __name__ == "__main__":
    unittest.main()
