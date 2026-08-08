from __future__ import annotations

import unittest

from topbook_features import NS_PER_SECOND
from topbook_features import aggregate_records


def quote(
    update_id: int,
    bid: float,
    bid_qty: float,
    ask: float,
    ask_qty: float,
    second: int,
):
    timestamp_ns = second * NS_PER_SECOND
    return (
        update_id,
        bid,
        bid_qty,
        ask,
        ask_qty,
        timestamp_ns,
        timestamp_ns,
    )


class TopBookFeatureTests(unittest.TestCase):
    def test_ask_defense_requires_depletion_then_persistent_refill(self) -> None:
        values = aggregate_records(
            [
                quote(1, 100.0, 4.0, 101.0, 10.0, 0),
                quote(2, 100.0, 4.0, 101.0, 5.0, 1),
                quote(3, 100.0, 4.0, 101.0, 12.0, 2),
            ],
        )[0]
        self.assertTrue(values["topbook_ask_persistent_refill"])
        self.assertEqual(values["topbook_ask_queue_response"], 1)

    def test_ask_withdrawal_needs_removal_and_best_ask_retreat(self) -> None:
        values = aggregate_records(
            [
                quote(1, 100.0, 5.0, 101.0, 10.0, 0),
                quote(2, 100.0, 5.0, 101.0, 4.0, 1),
                quote(3, 100.0, 5.0, 102.0, 3.0, 2),
            ],
        )[0]
        self.assertEqual(values["topbook_ask_queue_response"], -1)

    def test_bid_side_is_mirror_symmetric(self) -> None:
        values = aggregate_records(
            [
                quote(1, 100.0, 10.0, 101.0, 4.0, 0),
                quote(2, 100.0, 5.0, 101.0, 4.0, 1),
                quote(3, 100.0, 12.0, 101.0, 4.0, 2),
            ],
        )[0]
        self.assertTrue(values["topbook_bid_persistent_refill"])
        self.assertEqual(values["topbook_bid_queue_response"], 1)

    def test_additions_without_prior_depletion_are_not_refill(self) -> None:
        values = aggregate_records(
            [
                quote(1, 100.0, 5.0, 101.0, 5.0, 0),
                quote(2, 100.0, 6.0, 101.0, 6.0, 1),
            ],
        )[0]
        self.assertFalse(values["topbook_bid_persistent_refill"])
        self.assertFalse(values["topbook_ask_persistent_refill"])
        self.assertEqual(values["topbook_bid_queue_response"], 0)
        self.assertEqual(values["topbook_ask_queue_response"], 0)

    def test_refill_then_retreat_with_more_adds_than_removes_is_unresolved(self) -> None:
        values = aggregate_records(
            [
                quote(1, 100.0, 5.0, 101.0, 10.0, 0),
                quote(2, 100.0, 5.0, 101.0, 4.0, 1),
                quote(3, 100.0, 5.0, 101.0, 12.0, 2),
                quote(4, 100.0, 5.0, 102.0, 3.0, 3),
            ],
        )[0]
        self.assertEqual(values["topbook_ask_queue_response"], 0)
        self.assertFalse(values["topbook_ask_persistent_refill"])

    def test_minute_boundaries_do_not_share_queue_state(self) -> None:
        values = aggregate_records(
            [
                quote(1, 100.0, 5.0, 101.0, 5.0, 0),
                quote(2, 100.0, 5.0, 101.0, 5.0, 61),
            ],
        )
        self.assertEqual(len(values), 2)
        self.assertEqual(values[0]["topbook_quote_updates"], 1)
        self.assertEqual(values[1]["topbook_quote_updates"], 1)

    def test_non_monotonic_observed_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            aggregate_records(
                [
                    quote(1, 100.0, 5.0, 101.0, 5.0, 2),
                    quote(2, 100.0, 5.0, 101.0, 5.0, 1),
                ],
            )


if __name__ == "__main__":
    unittest.main()
