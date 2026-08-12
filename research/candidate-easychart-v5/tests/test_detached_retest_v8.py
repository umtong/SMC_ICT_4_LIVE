from __future__ import annotations

import unittest

from contracts_v5 import Pivot, ScenarioPath, SetupState
from domain import Candle, Side
from scenario_detached_retest_v8 import DetachedRetestScenarioEngine, fully_detached

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


def make_engine() -> DetachedRetestScenarioEngine:
    return DetachedRetestScenarioEngine(
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


class DetachedRetestTests(unittest.TestCase):
    def test_entire_bar_must_leave_zone_before_return(self) -> None:
        self.assertFalse(fully_detached(Side.LONG, 100.0, 101.0, candle(1, 101, 102, 101, 101.5)))
        self.assertTrue(fully_detached(Side.LONG, 100.0, 101.0, candle(1, 101.2, 102, 101.1, 101.5)))
        self.assertFalse(fully_detached(Side.SHORT, 100.0, 101.0, candle(1, 100, 100.0, 99, 99.5)))
        self.assertTrue(fully_detached(Side.SHORT, 100.0, 101.0, candle(1, 99.8, 99.9, 99, 99.5)))
        with self.assertRaises(ValueError):
            fully_detached(Side.LONG, 101.0, 100.0, candle(1, 100, 101, 99, 100))

    def test_acceptance_contact_before_detachment_is_not_a_retest(self) -> None:
        engine = make_engine()
        target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
        source = pivot("HIGH_SOURCE", "HIGH", 110.0, 3, 5)
        origin = pivot("LOW_ORIGIN", "LOW", 107.0, 5, 7)
        add_pivots(engine, target, source, origin)

        engine.on_bar(5, candle(10, 108, 109, 107.5, 108.5))
        engine.on_bar(5, candle(11, 108.5, 112, 108, 111.0))
        setup = list(engine._active.values())[0]
        self.assertIs(setup.path, ScenarioPath.ACCEPTANCE)
        engine.on_bar(5, candle(12, 111.2, 112.0, 110.8, 111.5))
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

    def test_footprint_contact_before_detachment_is_not_a_retest(self) -> None:
        engine = make_engine()
        source = pivot("LOW_SOURCE", "LOW", 100.0, 1, 3)
        target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
        add_pivots(engine, target, source)

        engine.on_bar(5, candle(10, 105, 106, 104, 105))
        engine.on_bar(5, candle(11, 101, 102, 99.5, 101))
        setup = list(engine._active.values())[0]
        engine.on_bar(1, candle(12, 101.0, 101.2, 99.8, 100.5))
        engine.on_bar(1, candle(13, 100.4, 102.2, 99.7, 102.0))
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


if __name__ == "__main__":
    unittest.main()
