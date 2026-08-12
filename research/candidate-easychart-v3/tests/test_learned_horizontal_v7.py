from __future__ import annotations

import unittest

from contracts_v5 import ObjectKind, StructureFamily, StructureZone
from domain import Candle, Side
from easychart_zones import ZoneSide
from learned_horizontal_v7 import (
    LearnedHorizontalDetector,
    LearnedHorizontalScenarioEngine,
    LearnedHorizontalZone,
    LearnedSetupState,
)


def candle(
    timestamp: int,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> Candle:
    return Candle(timestamp, open_, high, low, close, 1.0)


def learned_zone(
    side: ZoneSide = ZoneSide.SUPPORT,
    lower: float = 99.0,
    upper: float = 100.0,
    zone_id: str = "LEARNED",
    touches: int = 2,
) -> LearnedHorizontalZone:
    kind = (
        ObjectKind.HORIZONTAL_SUPPORT
        if side is ZoneSide.SUPPORT
        else ObjectKind.HORIZONTAL_RESISTANCE
    )
    return LearnedHorizontalZone(
        zone_id=zone_id,
        kind=kind,
        family=StructureFamily.HORIZONTAL,
        side=side,
        timeframe_minutes=15,
        lower=lower,
        upper=upper,
        invalidation=lower - 0.1 if side is ZoneSide.SUPPORT else upper + 0.1,
        impulse_extreme=lower if side is ZoneSide.SUPPORT else upper,
        formed_index=1,
        formed_time_ns=1,
        observed_time_ns=3,
        formation_indices=(1, 2),
        strength_ratio=1.5,
        source_structure_id=zone_id,
        source_pivot_span=1,
        touch_count=touches,
        member_ids=(f"{zone_id}:A", f"{zone_id}:B"),
    )


class ObjectiveBook:
    def __init__(self, target: float | None = 110.0) -> None:
        self.target = target

    def target_for(self, side: Side, **_: object) -> tuple[StructureZone, float] | None:
        if self.target is None:
            return None
        price = self.target
        kind = (
            ObjectKind.HORIZONTAL_RESISTANCE
            if side is Side.LONG
            else ObjectKind.HORIZONTAL_SUPPORT
        )
        zone_side = ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT
        target = StructureZone(
            zone_id="TARGET",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=zone_side,
            timeframe_minutes=15,
            lower=price,
            upper=price + 0.1,
            invalidation=price + 0.2 if side is Side.LONG else price - 0.1,
            impulse_extreme=price,
            formed_index=0,
            formed_time_ns=0,
            observed_time_ns=0,
            formation_indices=(),
            strength_ratio=1.0,
            source_structure_id="TARGET",
            source_pivot_span=1,
        )
        return target, price


def make_engine(target: float | None = 110.0) -> LearnedHorizontalScenarioEngine:
    return LearnedHorizontalScenarioEngine(
        "TEST",
        0.1,
        scale_name="MICRO",
        context_minutes=15,
        trigger_minutes=1,
        objective_book=ObjectiveBook(target),
        minimum_gross_rr=1.0,
    )


def inject(engine: LearnedHorizontalScenarioEngine, zone: LearnedHorizontalZone) -> None:
    engine.detector.zones.append(zone)
    engine.detector._zone_ids.add(zone.zone_id)
    engine.detector._active_zones[zone.zone_id] = zone


class LearnedHorizontalDetectorTests(unittest.TestCase):
    def test_two_rejection_intervals_form_exact_intersection_after_confirmation(self) -> None:
        detector = LearnedHorizontalDetector("TEST", 15, 0.1, pivot_spans=(1,))
        bars = [
            candle(1, 103.0, 104.0, 102.0, 103.0),
            candle(2, 101.0, 102.0, 99.0, 100.5),
            candle(3, 102.0, 103.0, 101.0, 102.0),
            candle(4, 102.0, 103.0, 101.0, 102.0),
            candle(5, 101.0, 102.0, 99.5, 100.8),
        ]
        for bar in bars:
            self.assertFalse(detector.on_bar(bar))
        created = detector.on_bar(candle(6, 102.0, 103.0, 101.0, 102.0))
        self.assertEqual(len(created), 1)
        self.assertAlmostEqual(created[0].lower, 99.5)
        self.assertAlmostEqual(created[0].upper, 100.5)
        self.assertEqual(created[0].touch_count, 2)

    def test_same_physical_wick_across_spans_is_one_touch(self) -> None:
        detector = LearnedHorizontalDetector("TEST", 15, 0.1, pivot_spans=(1, 2))
        for bar in (
            candle(1, 102.0, 103.0, 101.0, 102.0),
            candle(2, 102.0, 103.0, 101.0, 102.0),
            candle(3, 101.0, 102.0, 99.0, 100.5),
            candle(4, 102.0, 103.0, 101.0, 102.0),
            candle(5, 102.0, 104.0, 101.0, 103.0),
        ):
            detector.on_bar(bar)
        supports = [item for item in detector.intervals if item.side is ZoneSide.SUPPORT]
        self.assertEqual(len(supports), 1)
        self.assertGreaterEqual(
            detector.diagnostics.get("same_physical_wick_span_duplicate", 0),
            1,
        )
        self.assertFalse(detector.zones)

    def test_nonoverlapping_rejections_do_not_create_level(self) -> None:
        detector = LearnedHorizontalDetector("TEST", 15, 0.1, pivot_spans=(1,))
        for bar in (
            candle(1, 104.0, 105.0, 103.0, 104.0),
            candle(2, 102.0, 103.0, 99.0, 100.0),
            candle(3, 104.0, 105.0, 103.0, 104.0),
            candle(4, 104.0, 105.0, 103.0, 104.0),
            candle(5, 103.0, 104.0, 101.0, 102.0),
            candle(6, 104.0, 105.0, 103.0, 104.0),
        ):
            detector.on_bar(bar)
        self.assertFalse(detector.zones)

    def test_pivot_is_not_available_before_right_bar_closes(self) -> None:
        detector = LearnedHorizontalDetector("TEST", 15, 0.1, pivot_spans=(1,))
        detector.on_bar(candle(1, 102.0, 103.0, 101.0, 102.0))
        detector.on_bar(candle(2, 101.0, 102.0, 99.0, 100.5))
        self.assertFalse(detector.intervals)
        detector.on_bar(candle(3, 102.0, 103.0, 101.0, 102.0))
        self.assertEqual(len(detector.intervals), 1)


class LearnedHorizontalScenarioTests(unittest.TestCase):
    def test_long_fakeout_first_retest_creates_plan(self) -> None:
        engine = make_engine(110.0)
        inject(engine, learned_zone())
        self.assertFalse(engine.on_bar(15, candle(10, 101.0, 102.0, 98.0, 101.0)))
        plans = engine.on_bar(1, candle(11, 100.4, 101.5, 99.5, 101.2))
        self.assertEqual(len(plans), 1)
        self.assertIs(plans[0].side, Side.LONG)
        self.assertEqual(plans[0].scenario_path, "FAKEOUT")
        self.assertAlmostEqual(plans[0].stop, 97.9)

    def test_short_fakeout_first_retest_creates_plan(self) -> None:
        engine = make_engine(90.0)
        inject(
            engine,
            learned_zone(ZoneSide.RESISTANCE, 100.0, 101.0, "RESISTANCE"),
        )
        engine.on_bar(15, candle(10, 100.0, 102.0, 99.0, 99.5))
        plans = engine.on_bar(1, candle(11, 100.5, 100.8, 98.5, 99.2))
        self.assertEqual(len(plans), 1)
        self.assertIs(plans[0].side, Side.SHORT)
        self.assertAlmostEqual(plans[0].stop, 102.1)

    def test_next_owner_outside_is_accepted_break_not_reversal(self) -> None:
        engine = make_engine(110.0)
        inject(engine, learned_zone())
        engine.on_bar(15, candle(10, 101.0, 101.2, 98.0, 98.5))
        engine.on_bar(15, candle(20, 98.4, 98.8, 97.5, 98.2))
        self.assertFalse(engine._active)
        self.assertIs(engine.setups[0].state, LearnedSetupState.ACCEPTED_BREAK)
        self.assertFalse(engine.plans)

    def test_delayed_w_trap_reentry_then_first_retest(self) -> None:
        engine = make_engine(110.0)
        inject(engine, learned_zone())
        engine.on_bar(1, candle(9, 100.2, 100.5, 99.5, 100.1))
        engine.on_bar(15, candle(10, 101.0, 101.2, 97.8, 98.5))
        engine.on_bar(1, candle(11, 98.5, 98.8, 97.8, 98.2))
        engine.on_bar(1, candle(12, 98.2, 100.2, 98.1, 99.8))
        engine.on_bar(1, candle(13, 99.8, 100.0, 97.9, 98.4))
        engine.on_bar(1, candle(14, 98.4, 98.9, 97.7, 98.1))
        engine.on_bar(1, candle(15, 98.1, 99.3, 98.0, 99.0))
        setup = engine.setups[0]
        self.assertIsNotNone(setup.topology_confirmed_time_ns)
        engine.on_bar(15, candle(16, 98.8, 101.2, 98.0, 101.0))
        self.assertIs(setup.state, LearnedSetupState.WAITING_RETEST)
        plans = engine.on_bar(1, candle(17, 100.4, 101.5, 99.5, 101.1))
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].scenario_path, "TRAP_REENTRY")

    def test_reentry_without_topology_consumes_first_retest(self) -> None:
        engine = make_engine(110.0)
        inject(engine, learned_zone())
        engine.on_bar(15, candle(10, 101.0, 101.2, 98.0, 98.5))
        engine.on_bar(15, candle(20, 98.5, 101.2, 98.2, 101.0))
        setup = engine.setups[0]
        self.assertIs(setup.state, LearnedSetupState.REENTRY_PENDING_TOPOLOGY)
        self.assertFalse(engine.on_bar(1, candle(21, 100.5, 101.0, 99.5, 100.8)))
        self.assertIs(setup.state, LearnedSetupState.FIRST_RETEST_UNRESOLVED)

    def test_failed_first_fakeout_retest_is_not_retried(self) -> None:
        engine = make_engine(110.0)
        inject(engine, learned_zone())
        engine.on_bar(15, candle(10, 101.0, 102.0, 98.0, 101.0))
        self.assertFalse(engine.on_bar(1, candle(11, 100.5, 101.0, 99.5, 99.8)))
        self.assertIs(engine.setups[0].state, LearnedSetupState.FIRST_RETEST_UNRESOLVED)
        self.assertFalse(engine.on_bar(1, candle(12, 100.2, 101.5, 99.5, 101.2)))
        self.assertFalse(engine.plans)

    def test_both_sides_swept_is_unresolved(self) -> None:
        engine = make_engine(110.0)
        inject(engine, learned_zone())
        inject(
            engine,
            learned_zone(ZoneSide.RESISTANCE, 109.0, 110.0, "RESISTANCE"),
        )
        engine.on_bar(15, candle(10, 105.0, 111.0, 98.0, 105.0))
        self.assertFalse(engine._active)
        self.assertEqual(len(engine.setups), 2)
        self.assertTrue(
            all(
                setup.state is LearnedSetupState.BOTH_SIDES_UNRESOLVED
                for setup in engine.setups
            ),
        )

    def test_nested_same_side_sweep_is_one_episode(self) -> None:
        engine = make_engine(110.0)
        inject(engine, learned_zone(ZoneSide.SUPPORT, 99.0, 100.0, "NEAR"))
        inject(engine, learned_zone(ZoneSide.SUPPORT, 97.0, 98.0, "FAR"))
        engine.on_bar(15, candle(10, 101.0, 102.0, 96.0, 101.0))
        active = [
            setup
            for setup in engine.setups
            if setup.state is LearnedSetupState.WAITING_RETEST
        ]
        duplicates = [
            setup
            for setup in engine.setups
            if setup.state is LearnedSetupState.DUPLICATE_EPISODE
        ]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].zone.zone_id, "NEAR")
        self.assertEqual(len(duplicates), 1)

    def test_no_preexisting_target_is_terminal(self) -> None:
        engine = make_engine(None)
        inject(engine, learned_zone())
        engine.on_bar(15, candle(10, 101.0, 102.0, 98.0, 101.0))
        self.assertIs(engine.setups[0].state, LearnedSetupState.NO_TARGET)
        self.assertFalse(engine._active)

    def test_target_spent_before_retest_is_terminal(self) -> None:
        engine = make_engine(102.0)
        inject(engine, learned_zone())
        engine.on_bar(15, candle(10, 101.0, 101.5, 98.0, 101.0))
        engine.on_bar(1, candle(11, 101.2, 102.2, 100.5, 101.8))
        self.assertIs(engine.setups[0].state, LearnedSetupState.TARGET_SPENT)
        self.assertFalse(engine.plans)


if __name__ == "__main__":
    unittest.main()
