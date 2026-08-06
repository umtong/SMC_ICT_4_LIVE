from __future__ import annotations

import unittest

from failed_acceptance_trap import build_failed_acceptance_trap
from lrb_types import BarObservation, PrimitiveSnapshot, ScenarioSignal


def snap(*, open_: float, high: float, low: float, close: float, flow: float) -> PrimitiveSnapshot:
    width = high - low
    return PrimitiveSnapshot(
        index=10,
        observation=BarObservation(1_000, open_, high, low, close, 1000.0, 500.0 * (flow + 1.0), 100),
        ready=True,
        atr=1.0,
        rel_volume=1.5,
        flow_ratio=flow,
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=0.0,
        lower_wick_fraction=0.0,
        close_location=(close - low) / width,
        upper_fast=103.0,
        lower_fast=97.0,
        upper_slow=105.0,
        lower_slow=95.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


def signal(direction: str) -> ScenarioSignal:
    if direction == "LONG":
        range_high, range_low = 100.0, 90.0
        episode_extreme = 104.0
    else:
        range_high, range_low = 110.0, 100.0
        episode_extreme = 96.0
    return ScenarioSignal(
        scenario_id="trap",
        family="SAC",
        direction=direction,
        observed_ts_ns=900,
        reference_entry=102.0 if direction == "LONG" else 98.0,
        stop_price=99.9 if direction == "LONG" else 100.1,
        target_price=110.0 if direction == "LONG" else 90.0,
        target_reason="ORIGINAL_CONTINUATION",
        atr=1.0,
        liquidity_level=100.0,
        details={
            "episode_extreme": episode_extreme,
            "auction_range_high": range_high,
            "auction_range_low": range_low,
        },
    )


class FailedAcceptanceTrapTests(unittest.TestCase):
    def params(self, action: str):
        return {
            "sac_failed_defense_action": action,
            "stop_buffer_atr": 0.10,
            "minimum_structural_rr": 0.75,
        }

    def test_upper_acceptance_reentry_arms_short_trap(self) -> None:
        result = build_failed_acceptance_trap(
            signal("LONG"),
            snap(open_=101.0, high=101.5, low=98.5, close=99.0, flow=-0.2),
            self.params("TRAP_RECLAIM_BODY"),
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.family, "FAT")
        self.assertEqual(result.direction, "SHORT")
        self.assertAlmostEqual(result.stop_price, 104.1)
        self.assertEqual(result.target_price, 95.0)

    def test_lower_acceptance_reentry_is_symmetric(self) -> None:
        result = build_failed_acceptance_trap(
            signal("SHORT"),
            snap(open_=99.0, high=101.5, low=98.5, close=101.0, flow=0.2),
            self.params("TRAP_RECLAIM_BODY"),
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.direction, "LONG")
        self.assertAlmostEqual(result.stop_price, 95.9)
        self.assertEqual(result.target_price, 105.0)

    def test_boundary_not_reclaimed_does_not_arm(self) -> None:
        result = build_failed_acceptance_trap(
            signal("LONG"),
            snap(open_=102.0, high=103.0, low=100.5, close=101.0, flow=-0.3),
            self.params("TRAP_RECLAIM_BODY"),
        )
        self.assertIsNone(result)

    def test_flow_variant_requires_opposite_flow(self) -> None:
        snapshot = snap(open_=101.0, high=101.5, low=98.5, close=99.0, flow=0.2)
        self.assertIsNotNone(
            build_failed_acceptance_trap(signal("LONG"), snapshot, self.params("TRAP_RECLAIM_BODY")),
        )
        self.assertIsNone(
            build_failed_acceptance_trap(signal("LONG"), snapshot, self.params("TRAP_RECLAIM_BODY_FLOW")),
        )


if __name__ == "__main__":
    unittest.main()
