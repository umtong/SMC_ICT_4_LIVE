from __future__ import annotations

import unittest

from contracts_v5 import Pivot, SetupState
from domain import Candle
from event_footprints_v5 import EventLocalZoneDetector
from scenario_engine_v5 import StructureScenarioEngine

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


def make_engine() -> StructureScenarioEngine:
    return StructureScenarioEngine(
        "TEST",
        0.1,
        scale_name="MACRO",
        higher_minutes=60,
        decision_minutes=15,
        trigger_minutes=5,
        minimum_gross_rr=1.0,
    )


def add_pivots(engine: StructureScenarioEngine, *items: Pivot) -> None:
    engine.structure.pivots.extend(items)
    engine.structure._pivot_ids.update(item.pivot_id for item in items)
    engine.structure._active_pivots.update({item.pivot_id: item for item in items})


class EventLocalFootprintTests(unittest.TestCase):
    def test_detector_does_not_scan_historical_zone_lifecycle(self) -> None:
        detector = EventLocalZoneDetector("TEST", 1, 0.1)
        detector._update_lifecycle = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("scan"))
        detector.on_bar(candle(1, 101.0, 101.2, 99.8, 100.0))
        detector.on_bar(candle(2, 99.9, 101.5, 99.7, 101.3))
        self.assertTrue(detector.zones)

    def test_selected_footprint_invalidation_is_terminal_before_retest(self) -> None:
        engine = make_engine()
        source = pivot("LOW_SOURCE", "LOW", 100.0, 1, 3)
        target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
        add_pivots(engine, target, source)
        engine.on_bar(15, candle(10, 105, 106, 104, 105))
        engine.on_bar(15, candle(11, 101, 102, 99.5, 101))
        setup = list(engine._active.values())[0]
        engine.on_bar(5, candle(12, 101.0, 101.2, 99.8, 100.5))
        engine.on_bar(5, candle(13, 100.4, 102.2, 99.7, 102.0))
        self.assertIs(setup.state, SetupState.WAITING_FOOTPRINT_RETEST)
        assert setup.trigger_zone is not None
        invalidation = setup.trigger_zone.invalidation
        engine.on_bar(5, candle(14, invalidation + 0.2, 101.0, invalidation, invalidation + 0.1))
        self.assertIs(setup.state, SetupState.INVALIDATED)
        self.assertEqual(setup.terminal_reason, "trigger_footprint_invalidated_before_retest")
        self.assertFalse(engine.plans)


if __name__ == "__main__":
    unittest.main()
