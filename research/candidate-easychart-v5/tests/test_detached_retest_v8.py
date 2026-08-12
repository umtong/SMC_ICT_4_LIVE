from __future__ import annotations

import unittest

from contracts_v5 import Pivot, ScenarioPath, SetupState
from domain import Candle, Side
from scenario_detached_retest_v8 import (
    CLOSE_DETACHED_RETEST_RULE,
    CloseDetachedRetestScenarioEngine,
    DetachedRetestScenarioEngine,
    close_detached,
    fully_detached,
)

NS = 60_000_000_000


def candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(index * NS, open_, high, low, close, 1.0)


def pivot(
    pivot_id: str,
    side: str,
    price: float,
    event_index: int,
    observed_index: int,
    span: int = 2,
) -> Pivot:
    return Pivot(
        pivot_id,
        side,
        price,
        event_index,
        event_index * NS,
        observed_index,
        observed_index * NS,
        span,
        2.0,
    )


def make_engine(
    engine_type: type[DetachedRetestScenarioEngine] = DetachedRetestScenarioEngine,
) -> DetachedRetestScenarioEngine:
    return engine_type(
        "TEST",
        0.1,
        scale_name="MICRO",
        higher_minutes=15,
        decision_minutes=5,
        trigger_minutes=1,
        minimum_gross_rr=1.0,
    )


def add_pivots(engine: DetachedRetestScenarioEngine, *items: Pivot) -> None:
    engine.structure.pivots.extend(items)
    engine.structure._pivot_ids.update(item.pivot_id for item in items)
    engine.structure._active_pivots.update({item.pivot_id: item for item in items})


def arm_acceptance(engine: DetachedRetestScenarioEngine):  # type: ignore[no-untyped-def]
    target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
    source = pivot("HIGH_SOURCE", "HIGH", 110.0, 3, 5)
    origin = pivot("LOW_ORIGIN", "LOW", 107.0, 5, 7)
    add_pivots(engine, target, source, origin)
    engine.on_bar(5, candle(10, 108, 109, 107.5, 108.5))
    engine.on_bar(5, candle(11, 108.5, 112, 108, 111.0))
    setup = list(engine._active.values())[0]
    engine.on_bar(5, candle(12, 111.2, 112.0, 110.8, 111.5))
    return setup


def arm_footprint(engine: DetachedRetestScenarioEngine):  # type: ignore[no-untyped-def]
    source = pivot("LOW_SOURCE", "LOW", 100.0, 1, 3)
    target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
    add_pivots(engine, target, source)
    engine.on_bar(5, candle(10, 105, 106, 104, 105))
    engine.on_bar(5, candle(11, 101, 102, 99.5, 101))
    setup = list(engine._active.values())[0]
    engine.on_bar(1, candle(12, 101.0, 101.2, 99.8, 100.5))
    engine.on_bar(1, candle(13, 100.4, 102.2, 99.7, 102.0))
    return setup


class DetachedRetestTests(unittest.TestCase):
    def test_entire_bar_must_leave_zone_before_return(self) -> None:
        self.assertFalse(fully_detached(Side.LONG, 100.0, 101.0, candle(1, 101, 102, 101, 101.5)))
        self.assertTrue(fully_detached(Side.LONG, 100.0, 101.0, candle(1, 101.2, 102, 101.1, 101.5)))
        self.assertFalse(fully_detached(Side.SHORT, 100.0, 101.0, candle(1, 100, 100.0, 99, 99.5)))
        self.assertTrue(fully_detached(Side.SHORT, 100.0, 101.0, candle(1, 99.8, 99.9, 99, 99.5)))
        with self.assertRaises(ValueError):
            fully_detached(Side.LONG, 101.0, 100.0, candle(1, 100, 101, 99, 100))

    def test_completed_close_can_detach_while_wick_still_overlaps(self) -> None:
        long_bar = candle(1, 100.8, 102.0, 100.5, 101.5)
        short_bar = candle(2, 100.2, 100.5, 99.0, 99.5)
        self.assertFalse(fully_detached(Side.LONG, 100.0, 101.0, long_bar))
        self.assertTrue(close_detached(Side.LONG, 100.0, 101.0, long_bar))
        self.assertFalse(fully_detached(Side.SHORT, 100.0, 101.0, short_bar))
        self.assertTrue(close_detached(Side.SHORT, 100.0, 101.0, short_bar))
        with self.assertRaises(ValueError):
            close_detached(Side.SHORT, 101.0, 100.0, short_bar)

    def test_acceptance_contact_before_full_detachment_is_not_a_retest(self) -> None:
        engine = make_engine()
        setup = arm_acceptance(engine)
        self.assertIs(setup.path, ScenarioPath.ACCEPTANCE)
        self.assertIs(setup.state, SetupState.WAITING_ACCEPTANCE_RETEST)

        self.assertFalse(engine.on_bar(1, candle(13, 111.0, 112.0, 110.0, 111.4)))
        self.assertIs(setup.state, SetupState.WAITING_ACCEPTANCE_RETEST)
        self.assertNotIn(setup.setup_id, engine._detached_setup_ids)

        self.assertFalse(engine.on_bar(1, candle(14, 111.4, 112.5, 111.0, 112.0)))
        self.assertIn(setup.setup_id, engine._detached_setup_ids)

        plans = engine.on_bar(1, candle(15, 111.2, 112.0, 110.0, 111.4))
        self.assertEqual(len(plans), 1)
        self.assertIs(plans[0].side, Side.LONG)
        self.assertEqual(plans[0].scenario_path, "ACCEPTANCE")
        self.assertNotIn(setup.setup_id, engine._detached_setup_ids)

    def test_close_detachment_bar_cannot_also_be_acceptance_retest(self) -> None:
        engine = make_engine(CloseDetachedRetestScenarioEngine)
        setup = arm_acceptance(engine)
        self.assertIs(setup.state, SetupState.WAITING_ACCEPTANCE_RETEST)

        # The close is above the boundary but the wick still overlaps it. This
        # bar arms the return and is deliberately not allowed to enter itself.
        self.assertFalse(engine.on_bar(1, candle(13, 111.0, 112.0, 110.0, 111.4)))
        self.assertIn(setup.setup_id, engine._detached_setup_ids)
        self.assertIs(setup.state, SetupState.WAITING_ACCEPTANCE_RETEST)

        plans = engine.on_bar(1, candle(14, 111.2, 112.0, 110.0, 111.4))
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].scenario_path, "ACCEPTANCE")
        self.assertNotIn(setup.setup_id, engine._detached_setup_ids)
        self.assertEqual(engine.RETEST_RULE, CLOSE_DETACHED_RETEST_RULE)

    def test_footprint_contact_before_full_detachment_is_not_a_retest(self) -> None:
        engine = make_engine()
        setup = arm_footprint(engine)
        self.assertIs(setup.state, SetupState.WAITING_FOOTPRINT_RETEST)

        self.assertFalse(engine.on_bar(1, candle(14, 101.2, 102.0, 100.6, 101.5)))
        self.assertIs(setup.state, SetupState.WAITING_FOOTPRINT_RETEST)
        self.assertNotIn(setup.setup_id, engine._detached_setup_ids)

        self.assertFalse(engine.on_bar(1, candle(15, 101.5, 102.5, 101.2, 102.2)))
        self.assertIn(setup.setup_id, engine._detached_setup_ids)

        plans = engine.on_bar(1, candle(16, 101.2, 102.2, 100.6, 101.8))
        self.assertEqual(len(plans), 1)
        self.assertIs(plans[0].side, Side.LONG)
        self.assertEqual(plans[0].scenario_path, "REJECTION")
        self.assertNotIn(setup.setup_id, engine._detached_setup_ids)

    def test_close_detachment_bar_cannot_also_be_footprint_retest(self) -> None:
        engine = make_engine(CloseDetachedRetestScenarioEngine)
        setup = arm_footprint(engine)
        self.assertIs(setup.state, SetupState.WAITING_FOOTPRINT_RETEST)

        self.assertFalse(engine.on_bar(1, candle(14, 101.2, 102.0, 100.6, 101.5)))
        self.assertIn(setup.setup_id, engine._detached_setup_ids)
        self.assertIs(setup.state, SetupState.WAITING_FOOTPRINT_RETEST)

        plans = engine.on_bar(1, candle(15, 101.2, 102.2, 100.6, 101.8))
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].scenario_path, "REJECTION")
        self.assertNotIn(setup.setup_id, engine._detached_setup_ids)


if __name__ == "__main__":
    unittest.main()
