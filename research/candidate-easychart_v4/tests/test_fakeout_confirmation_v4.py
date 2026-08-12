from __future__ import annotations

import unittest

from domain import Candle
from scenario_bundle_v4 import StructuralSetupState
from scenario_runtime_v4 import CausalStructuralScenarioEngine

NS = 60_000_000_000


def bar(i: int, o: float, h: float, l: float, c: float, minutes: int = 60) -> Candle:
    return Candle((i + 1) * minutes * NS, o, h, l, c, 1.0)


class FakeoutConfirmationV4Tests(unittest.TestCase):
    def _engine_with_fakeout(self) -> tuple[CausalStructuralScenarioEngine, object, Candle]:
        engine = CausalStructuralScenarioEngine(
            "TEST",
            0.1,
            scale_name="TEST",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=1.0,
        )
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
        fakeout = bar(5, 13.0, 14.0, 11.4, 12.8)
        engine.on_bar(60, fakeout)
        setup = engine.setups[-1]
        self.assertEqual(setup.state, StructuralSetupState.WAITING_DISPLACEMENT)
        self.assertEqual(engine._pending_fakeout_confirmation[setup.setup_id], fakeout.high)
        return engine, setup, fakeout

    def test_lower_bars_cannot_arm_fakeout_before_context_confirmation(self) -> None:
        engine, setup, fakeout = self._engine_with_fakeout()
        lower = Candle(
            ts_close_ns=fakeout.ts_close_ns + 5 * NS,
            open=12.7,
            high=13.6,
            low=12.4,
            close=13.4,
            volume=1.0,
        )
        engine.on_bar(5, lower)
        self.assertEqual(setup.state, StructuralSetupState.WAITING_DISPLACEMENT)
        self.assertEqual(setup.trigger_zones, ())
        self.assertIn(setup.setup_id, engine._pending_fakeout_confirmation)

    def test_next_context_bar_must_close_beyond_fakeout_opposite_extreme(self) -> None:
        engine, setup, fakeout = self._engine_with_fakeout()
        failed = Candle(
            ts_close_ns=fakeout.ts_close_ns + 60 * NS,
            open=12.9,
            high=13.8,
            low=12.0,
            close=13.2,
            volume=1.0,
        )
        engine.on_bar(60, failed)
        self.assertEqual(setup.state, StructuralSetupState.INVALIDATED)
        self.assertNotIn(setup.setup_id, engine._active)
        self.assertNotIn(setup.setup_id, engine._pending_fakeout_confirmation)
        self.assertEqual(engine.diagnostics.get("fakeout_next_context_bar_failed_reversal"), 1)

    def test_confirmed_fakeout_waits_for_later_event_local_displacement(self) -> None:
        engine, setup, fakeout = self._engine_with_fakeout()
        confirmed = Candle(
            ts_close_ns=fakeout.ts_close_ns + 60 * NS,
            open=12.9,
            high=14.4,
            low=12.0,
            close=14.2,
            volume=1.0,
        )
        engine.on_bar(60, confirmed)
        self.assertEqual(setup.state, StructuralSetupState.WAITING_DISPLACEMENT)
        self.assertIn(setup.setup_id, engine._active)
        self.assertNotIn(setup.setup_id, engine._pending_fakeout_confirmation)
        self.assertEqual(engine.diagnostics.get("fakeout_context_reversal_confirmed"), 1)


if __name__ == "__main__":
    unittest.main()
