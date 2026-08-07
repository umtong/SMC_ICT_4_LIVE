from __future__ import annotations

from dataclasses import dataclass
import unittest

from candidate16_failed_far import SubmittedFarContext, strict_failed_far_target
from logic import Direction, Side


@dataclass
class Pool:
    scenario_id: str
    side: Side
    level: float
    strength: int = 1
    consumed: bool = False
    expiry_index: int = 100


class Engine:
    def __init__(self, pools: list[Pool], index: int = 10) -> None:
        self.pools = pools
        self._index = index


def context(side: Side, boundary: float = 100.0) -> SubmittedFarContext:
    return SubmittedFarContext(
        parent_scenario_id="ORIGINAL",
        pool_side=side,
        pool_level=boundary,
        pool_source="TEST",
        source_strength=2,
        boundary=boundary,
        original_direction=Direction.SHORT if side == Side.HIGH else Direction.LONG,
        original_entry=99.0,
        original_stop=100.0,
        original_target=95.0,
        atr=1.0,
        sweep_ts_ns=1,
        stop_model="SWEEP_EXTREME_INVALIDATION",
    )


class StrictTargetTests(unittest.TestCase):
    def test_high_side_excludes_parent_boundary_and_selects_nearest_strict_pool(self) -> None:
        pools = [
            Pool("ORIGINAL", Side.HIGH, 100.0),
            Pool("DUPLICATE", Side.HIGH, 100.0),
            Pool("BEHIND_REFERENCE", Side.HIGH, 100.4),
            Pool("NEAREST", Side.HIGH, 101.0),
            Pool("FARTHER", Side.HIGH, 103.0),
            Pool("OTHER_SIDE", Side.LOW, 90.0),
        ]
        selected = strict_failed_far_target(Engine(pools), context(Side.HIGH), 100.5)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.scenario_id, "NEAREST")

    def test_low_side_is_symmetric(self) -> None:
        pools = [
            Pool("ORIGINAL", Side.LOW, 100.0),
            Pool("DUPLICATE", Side.LOW, 100.0),
            Pool("BEHIND_REFERENCE", Side.LOW, 99.6),
            Pool("NEAREST", Side.LOW, 99.0),
            Pool("FARTHER", Side.LOW, 97.0),
        ]
        selected = strict_failed_far_target(Engine(pools), context(Side.LOW), 99.5)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.scenario_id, "NEAREST")

    def test_consumed_expired_and_weak_pools_are_rejected(self) -> None:
        pools = [
            Pool("CONSUMED", Side.HIGH, 101.0, consumed=True),
            Pool("EXPIRED", Side.HIGH, 102.0, expiry_index=9),
            Pool("WEAK", Side.HIGH, 103.0, strength=0),
        ]
        self.assertIsNone(strict_failed_far_target(Engine(pools), context(Side.HIGH), 100.2))

    def test_target_must_be_beyond_both_reference_and_boundary(self) -> None:
        pools = [Pool("BETWEEN", Side.HIGH, 101.0), Pool("AHEAD", Side.HIGH, 102.1)]
        selected = strict_failed_far_target(Engine(pools), context(Side.HIGH, 100.0), 102.0)
        self.assertEqual(selected.scenario_id, "AHEAD")


if __name__ == "__main__":
    unittest.main(verbosity=2)
