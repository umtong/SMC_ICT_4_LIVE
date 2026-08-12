from __future__ import annotations

import unittest

from contracts_v5 import Pivot, ScenarioPath, SetupState
from domain import Candle
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
    span: int = 6,
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
        scale_name="MICRO",
        higher_minutes=15,
        decision_minutes=5,
        trigger_minutes=1,
        minimum_gross_rr=1.0,
    )


def add_pivots(engine: StructureScenarioEngine, *items: Pivot) -> None:
    engine.structure.pivots.extend(items)
    engine.structure._pivot_ids.update(item.pivot_id for item in items)
    engine.structure._active_pivots.update({item.pivot_id: item for item in items})


class FastFakeoutSemanticsTests(unittest.TestCase):
    def test_close_back_inside_without_dominant_excursion_wick_is_unresolved(self) -> None:
        engine = make_engine()
        support = pivot("LOW_SOURCE", "LOW", 100.0, 1, 3)
        target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
        add_pivots(engine, target, support)

        engine.on_bar(5, candle(10, 105.0, 106.0, 104.0, 105.0))
        # The bar breaches support and closes above it, but the lower wick is
        # only 0.1 while the real body is 1.4. This is an ordinary strong close,
        # not the source's long-tail fast fakeout.
        engine.on_bar(5, candle(11, 99.8, 101.5, 99.7, 101.2))

        self.assertFalse(engine._active)
        setup = engine.setups[-1]
        self.assertIs(setup.path, ScenarioPath.REJECTION)
        self.assertIs(setup.state, SetupState.UNRESOLVED)
        self.assertEqual(
            setup.terminal_reason,
            "fast_fakeout_without_dominant_excursion_wick",
        )
        self.assertEqual(
            engine.diagnostics.get("fast_fakeout_without_dominant_excursion_wick"),
            1,
        )

    def test_long_excursion_wick_arms_first_later_structure_retest(self) -> None:
        engine = make_engine()
        support = pivot("LOW_SOURCE", "LOW", 100.0, 1, 3)
        target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
        add_pivots(engine, target, support)

        engine.on_bar(5, candle(10, 105.0, 106.0, 104.0, 105.0))
        engine.on_bar(5, candle(11, 101.0, 102.0, 99.5, 101.1))

        active = list(engine._active.values())
        self.assertEqual(len(active), 1)
        self.assertIs(active[0].path, ScenarioPath.REJECTION)
        self.assertIs(active[0].state, SetupState.WAITING_REJECTION_RETEST)
        self.assertEqual(active[0].confirmation_time_ns, 11 * NS)

    def test_upper_wick_translation_is_symmetric_for_short_fakeout(self) -> None:
        engine = make_engine()
        target = pivot("LOW_TARGET", "LOW", 80.0, 0, 2)
        resistance = pivot("HIGH_SOURCE", "HIGH", 100.0, 1, 3)
        add_pivots(engine, target, resistance)

        engine.on_bar(5, candle(10, 95.0, 96.0, 94.0, 95.0))
        engine.on_bar(5, candle(11, 99.9, 100.6, 98.8, 99.8))

        active = list(engine._active.values())
        self.assertEqual(len(active), 1)
        self.assertIs(active[0].path, ScenarioPath.REJECTION)
        self.assertIs(active[0].state, SetupState.WAITING_REJECTION_RETEST)


if __name__ == "__main__":
    unittest.main()
