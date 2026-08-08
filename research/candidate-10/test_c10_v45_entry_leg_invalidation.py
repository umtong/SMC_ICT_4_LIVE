from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from logic import Direction, Scenario, TradePlan

from c10_v45_overlay import reframe_entry_leg_invalidation
from c10_v45_overlay import reframe_primary_target


def plan(
    *,
    direction: Direction = Direction.LONG,
    entry: float = 100.0,
    stop: float = 95.0,
    target: float = 105.0,
    zone_low: float = 99.0,
    zone_high: float = 100.0,
) -> TradePlan:
    risk = entry - stop if direction == Direction.LONG else stop - entry
    reward = target - entry if direction == Direction.LONG else entry - target
    return TradePlan(
        scenario_id="TEST-SCENARIO",
        scenario=Scenario.FAR,
        direction=direction,
        observed_ts_ns=500,
        expected_entry=entry,
        stop_price=stop,
        target_price=target,
        atr=1.0,
        loss_per_unit=risk,
        gain_per_unit=reward,
        net_r=reward / risk,
        reason_code="BASELINE",
        expire_ts_ns=1_000,
        details={
            "zone_low": zone_low,
            "zone_high": zone_high,
            "confirmation_close": (
                entry + 1.0 if direction == Direction.LONG else entry - 1.0
            ),
            "ce_rejection_primary": {
                "initial_raid_invalidation": stop,
                "final_retest_invalidation": stop,
                "source_equilibrium": target,
            },
        },
    )


def logic(
    *,
    buffer: float = 0.08,
    min_stop_atr: float = 0.08,
    min_net_r: float = 1.25,
    highs: list[tuple[int, int, float]] | None = None,
    lows: list[tuple[int, int, float]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        internal_highs=highs or [],
        internal_lows=lows or [],
        bars=[],
        config=SimpleNamespace(
            stop_buffer_atr=buffer,
            min_stop_atr=min_stop_atr,
            min_net_r=min_net_r,
            effective_maker_rate=0.0,
            effective_taker_rate=0.0,
        ),
    )


class V45EntryLegInvalidationTest(unittest.TestCase):
    def test_raid_stop_cell_is_exactly_unchanged(self) -> None:
        baseline = plan()
        with patch.dict(
            "os.environ",
            {"C10_V45_INVALIDATION_MODE": "SOURCE_RAID_EXTREME"},
        ):
            decision = reframe_entry_leg_invalidation(baseline, logic())
        self.assertTrue(decision.approved)
        self.assertIs(decision.plan, baseline)
        self.assertFalse(decision.details["applied"])

    def test_long_stop_uses_void_low_plus_frozen_buffer(self) -> None:
        baseline = plan()
        with patch.dict(
            "os.environ",
            {
                "C10_V45_INVALIDATION_MODE": (
                    "FIRST_DISPLACEMENT_VOID_FAR_EDGE"
                ),
            },
        ):
            decision = reframe_entry_leg_invalidation(baseline, logic())
        self.assertTrue(decision.approved)
        self.assertAlmostEqual(decision.plan.stop_price, 98.92)
        self.assertAlmostEqual(decision.plan.loss_per_unit, 1.08)
        self.assertAlmostEqual(decision.plan.net_r, 5.0 / 1.08)
        details = decision.plan.details["source_entry_leg_invalidation"]
        self.assertEqual(details["structural_boundary"], 99.0)
        self.assertEqual(details["original_source_raid_stop"], 95.0)

    def test_short_stop_uses_void_high_plus_frozen_buffer(self) -> None:
        baseline = plan(
            direction=Direction.SHORT,
            entry=100.0,
            stop=105.0,
            target=95.0,
            zone_low=100.0,
            zone_high=101.0,
        )
        with patch.dict(
            "os.environ",
            {
                "C10_V45_INVALIDATION_MODE": (
                    "FIRST_DISPLACEMENT_VOID_FAR_EDGE"
                ),
            },
        ):
            decision = reframe_entry_leg_invalidation(baseline, logic())
        self.assertTrue(decision.approved)
        self.assertAlmostEqual(decision.plan.stop_price, 101.08)
        self.assertAlmostEqual(decision.plan.loss_per_unit, 1.08)

    def test_void_stop_must_be_tighter_than_source_raid_stop(self) -> None:
        baseline = plan(stop=99.2)
        with patch.dict(
            "os.environ",
            {
                "C10_V45_INVALIDATION_MODE": (
                    "FIRST_DISPLACEMENT_VOID_FAR_EDGE"
                ),
            },
        ):
            decision = reframe_entry_leg_invalidation(baseline, logic())
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "ENTRY_LEG_INVALIDATION_NON_CAUSAL_PRICE_ORDER",
        )

    def test_existing_minimum_stop_atr_is_preserved(self) -> None:
        baseline = plan(zone_low=99.99, zone_high=100.0)
        with patch.dict(
            "os.environ",
            {
                "C10_V45_INVALIDATION_MODE": (
                    "FIRST_DISPLACEMENT_VOID_FAR_EDGE"
                ),
            },
        ):
            decision = reframe_entry_leg_invalidation(
                baseline,
                logic(buffer=0.0, min_stop_atr=0.08),
            )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "ENTRY_LEG_STOP_DISTANCE_BELOW_EXECUTION_FLOOR",
        )

    def test_void_stop_can_make_a_live_internal_target_economic(self) -> None:
        baseline = plan(target=105.0)
        market = logic(highs=[(100, 200, 101.5)])
        with patch.dict(
            "os.environ",
            {
                "C10_V45_INVALIDATION_MODE": (
                    "FIRST_DISPLACEMENT_VOID_FAR_EDGE"
                ),
                "C10_V44_PRIMARY_TARGET_MODE": (
                    "PRECONFIRMED_INTERNAL_LIQUIDITY"
                ),
            },
        ):
            invalidation = reframe_entry_leg_invalidation(baseline, market)
            self.assertTrue(invalidation.approved)
            target = reframe_primary_target(invalidation.plan, market)
        self.assertTrue(target.approved)
        self.assertEqual(target.plan.target_price, 101.5)
        self.assertGreaterEqual(target.plan.net_r, 1.25)


if __name__ == "__main__":
    unittest.main()
