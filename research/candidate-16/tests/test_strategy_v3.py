from __future__ import annotations

import unittest

from accepted_failure_router import AuctionLevel
from strategy_v3 import ordered_next_source_objectives


def level(
    level_id: str,
    kind: str,
    price: float,
    observed_index: int,
    *,
    horizon: int = 60,
    range_low: float = 90.0,
    range_high: float = 110.0,
) -> AuctionLevel:
    return AuctionLevel(
        level_id=level_id,
        kind=kind,
        price=price,
        horizon_minutes=horizon,
        range_start_ns=1,
        range_end_ns=2,
        range_high=range_high,
        range_low=range_low,
        range_midpoint=0.5 * (range_low + range_high),
        observed_index=observed_index,
    )


class NextSourceObjectiveTest(unittest.TestCase):
    def test_short_path_starts_at_failed_range_edge_then_moves_lower(self):
        breached = level("failed-high", "HIGH", 110.0, 10)
        objectives = ordered_next_source_objectives(
            levels=(
                level("near-low", "LOW", 88.0, 8),
                level("future-low", "LOW", 80.0, 13),
                level("wrong-kind", "HIGH", 70.0, 8),
            ),
            breached=breached,
            interaction_index=12,
            side=-1,
            entry=105.0,
        )
        self.assertEqual(
            [item.label for item in objectives],
            ["FAILED_SOURCE_OPPOSITE_EDGE", "NEXT_COMPLETED_SOURCE_BOUNDARY"],
        )
        self.assertEqual([item.price for item in objectives], [90.0, 88.0])
        self.assertEqual(objectives[1].level_id, "near-low")

    def test_long_path_excludes_post_interaction_and_inside_range_levels(self):
        breached = level(
            "failed-low",
            "LOW",
            90.0,
            10,
            range_low=90.0,
            range_high=110.0,
        )
        objectives = ordered_next_source_objectives(
            levels=(
                level("inside-high", "HIGH", 108.0, 7),
                level("next-high", "HIGH", 112.0, 7),
                level("future-high", "HIGH", 120.0, 13),
            ),
            breached=breached,
            interaction_index=12,
            side=1,
            entry=95.0,
        )
        self.assertEqual([item.price for item in objectives], [110.0, 112.0])
        self.assertNotIn("future-high", [item.level_id for item in objectives])

    def test_entry_already_beyond_paired_edge_uses_next_causal_boundary(self):
        breached = level("failed-low", "LOW", 90.0, 10)
        objectives = ordered_next_source_objectives(
            levels=(
                level("next-high", "HIGH", 115.0, 9),
                level("far-high", "HIGH", 120.0, 9),
            ),
            breached=breached,
            interaction_index=12,
            side=1,
            entry=112.0,
        )
        self.assertEqual([item.price for item in objectives], [115.0, 120.0])

    def test_invalid_side_is_rejected(self):
        with self.assertRaises(ValueError):
            ordered_next_source_objectives(
                levels=(),
                breached=level("x", "HIGH", 110.0, 1),
                interaction_index=2,
                side=0,
                entry=100.0,
            )


if __name__ == "__main__":
    unittest.main()
