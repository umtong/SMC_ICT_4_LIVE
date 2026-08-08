from __future__ import annotations

from types import SimpleNamespace
import unittest

from candidate16_failed_far import (
    SEMANTIC_REJECTED_FAR_REASONS,
    semantic_rejected_far_context,
)
from logic import Direction, Scenario, Side


def plan(direction: Direction = Direction.SHORT):
    return SimpleNamespace(
        scenario=Scenario.FAR,
        direction=direction,
        scenario_id="PARENT-FAR",
        expected_entry=101.0,
        stop_price=103.0,
        target_price=96.0,
        atr=1.25,
        details={
            "sweep_extreme": 102.5,
            "pool_level": 102.0,
            "pool_source": "TEST_SESSION",
            "source_strength": 2,
            "sweep_ts_ns": 123,
            "stop_model": "SWEEP_EXTREME_INVALIDATION",
        },
    )


class SemanticRejectedFarContextTests(unittest.TestCase):
    def test_only_directionally_meaningful_rejections_are_eligible(self) -> None:
        self.assertEqual(
            SEMANTIC_REJECTED_FAR_REASONS,
            {
                "SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM",
                "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION",
            },
        )
        self.assertIsNone(
            semantic_rejected_far_context(plan(), "SEMANTIC_FAR_WEAK_LOCAL_DISPLACEMENT")
        )

    def test_short_reversal_maps_to_high_side_continuation(self) -> None:
        context = semantic_rejected_far_context(
            plan(Direction.SHORT),
            "SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM",
        )
        self.assertIsNotNone(context)
        self.assertEqual(context.pool_side, Side.HIGH)
        self.assertEqual(context.original_direction, Direction.SHORT)
        self.assertEqual(context.boundary, 102.5)
        self.assertEqual(context.pool_source, "TEST_SESSION")

    def test_long_reversal_maps_to_low_side_continuation(self) -> None:
        context = semantic_rejected_far_context(
            plan(Direction.LONG),
            "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION",
        )
        self.assertIsNotNone(context)
        self.assertEqual(context.pool_side, Side.LOW)
        self.assertEqual(context.original_direction, Direction.LONG)

    def test_incomplete_plan_fails_closed(self) -> None:
        candidate = plan()
        del candidate.details["sweep_extreme"]
        self.assertIsNone(
            semantic_rejected_far_context(
                candidate,
                "SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM",
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
