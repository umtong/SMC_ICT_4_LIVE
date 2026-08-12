from __future__ import annotations

import unittest

from contracts_v5 import Pivot, ScenarioPath
from domain import Candle
from scenario_bundle_v5 import ResearchScenarioBundleV5
from scenario_engine_v5 import StructureScenarioEngine

NS = 60_000_000_000


def candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(index * NS, open_, high, low, close, 1.0)


def pivot(pivot_id: str, side: str, price: float, event_index: int, observed_index: int) -> Pivot:
    return Pivot(
        pivot_id,
        side,
        price,
        event_index,
        event_index * NS,
        observed_index,
        observed_index * NS,
        2,
        2.0,
    )


class InteractionOwnerTimeframeTests(unittest.TestCase):
    def make_owner_engine(self) -> StructureScenarioEngine:
        engine = StructureScenarioEngine(
            "TEST",
            0.1,
            scale_name="MACRO",
            higher_minutes=60,
            decision_minutes=15,
            trigger_minutes=5,
            interaction_minutes=60,
            minimum_gross_rr=1.0,
        )
        source = pivot("LOW_SOURCE", "LOW", 100.0, 1, 3)
        target = pivot("HIGH_TARGET", "HIGH", 120.0, 0, 2)
        engine.structure.pivots.extend([target, source])
        engine.structure._pivot_ids.update({target.pivot_id, source.pivot_id})
        engine.structure._active_pivots.update({target.pivot_id: target, source.pivot_id: source})
        return engine

    def test_intermediate_close_cannot_resolve_higher_structure(self) -> None:
        engine = self.make_owner_engine()
        engine.on_bar(15, candle(10, 105, 106, 104, 105))
        engine.on_bar(15, candle(11, 101, 102, 99.5, 101))
        self.assertFalse(engine.setups)
        self.assertFalse(engine._active)

    def test_owner_close_resolves_rejection(self) -> None:
        engine = self.make_owner_engine()
        engine.on_bar(60, candle(10, 105, 106, 104, 105))
        engine.on_bar(60, candle(11, 101, 102, 99.5, 101))
        self.assertEqual(len(engine._active), 1)
        setup = next(iter(engine._active.values()))
        self.assertIs(setup.path, ScenarioPath.REJECTION)
        self.assertEqual(setup.interaction_time_ns, 11 * NS)

    def test_integrated_bundle_assigns_structure_owner_timeframes(self) -> None:
        bundle = ResearchScenarioBundleV5("TEST", 0.1)
        self.assertEqual(bundle.macro.interaction_minutes, 60)
        self.assertEqual(bundle.micro.interaction_minutes, 15)


if __name__ == "__main__":
    unittest.main()
