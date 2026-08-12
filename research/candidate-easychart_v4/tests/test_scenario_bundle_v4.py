from __future__ import annotations

import unittest

from domain import Candle
from easychart_zones import PriceZone, ZoneKind, ZoneSide
from scenario_bundle_v4 import StructuralScenarioEngine, StructuralSetupState

NS = 60_000_000_000


def bar(i: int, o: float, h: float, l: float, c: float, minutes: int = 60) -> Candle:
    return Candle((i + 1) * minutes * NS, o, h, l, c, 1.0)


class StructuralScenarioV4Tests(unittest.TestCase):
    def _engine_with_channel_event(self) -> tuple[StructuralScenarioEngine, object]:
        engine = StructuralScenarioEngine(
            "TEST",
            0.1,
            scale_name="TEST",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=1.0,
        )
        # Use span=1 only to keep the fixture small. Production keeps spans 2/6.
        engine.structure.pivot_spans = (1,)
        seed = [
            bar(0, 12.0, 13.0, 11.8, 12.5),
            bar(1, 11.4, 12.2, 10.0, 11.5),
            bar(2, 13.0, 16.0, 12.0, 15.0),
            bar(3, 12.0, 14.0, 11.0, 13.0),
            bar(4, 13.5, 15.0, 12.5, 14.0),
        ]
        for candle in seed:
            engine.on_bar(60, candle)
        interaction = bar(5, 13.0, 14.0, 12.0, 13.2)
        events = engine.structure.on_bar(interaction)
        self.assertEqual(len(events), 1)
        engine._create_setups(events)
        return engine, interaction

    def test_structural_stop_is_not_replaced_by_tiny_trigger_stop(self) -> None:
        engine, interaction = self._engine_with_channel_event()
        setup = engine.setups[-1]
        event = setup.event
        trigger = PriceZone(
            zone_id="trigger",
            kind=ZoneKind.ORDER_BLOCK,
            side=ZoneSide.SUPPORT,
            timeframe_minutes=5,
            lower=11.8,
            upper=12.0,
            invalidation=11.79,
            impulse_extreme=13.0,
            formed_index=1,
            formed_time_ns=interaction.ts_close_ns,
            observed_time_ns=interaction.ts_close_ns,
            formation_indices=(0, 1),
            strength_ratio=2.5,
        )
        setup.trigger_zones = (trigger,)
        setup.state = StructuralSetupState.WAITING_RETEST
        entry_bar = Candle(
            ts_close_ns=interaction.ts_close_ns + 5 * NS,
            open=11.9,
            high=12.5,
            low=11.8,
            close=12.2,
            volume=1.0,
        )
        plan = engine._plan(setup, trigger, entry_bar)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.stop, event.stop_reference)
        self.assertNotEqual(plan.stop, trigger.invalidation)
        self.assertGreaterEqual(plan.gross_rr, 1.0)

    def test_target_is_structural_before_rr_gate(self) -> None:
        engine, _ = self._engine_with_channel_event()
        setup = engine.setups[-1]
        self.assertIsNotNone(setup.event.target_boundary_id)
        target = engine.structure.find_boundary(setup.event.target_boundary_id or "")
        self.assertIsNotNone(target)


if __name__ == "__main__":
    unittest.main()
