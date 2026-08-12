from __future__ import annotations

import unittest

from contracts_v5 import ObjectKind, StructureFamily, StructureZone
from domain import Candle, Side
from easychart_zones import ZoneSide
from learned_horizontal_v7 import (
    LearnedHorizontalScenarioEngine,
    LearnedHorizontalZone,
    LearnedSetupState,
)
import learned_horizontal_v7_runtime  # noqa: F401 - applies tested lifecycle repair


def candle(timestamp: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(timestamp, open_, high, low, close, 1.0)


def learned_zone() -> LearnedHorizontalZone:
    return LearnedHorizontalZone(
        zone_id="LEARNED_SUPPORT",
        kind=ObjectKind.HORIZONTAL_SUPPORT,
        family=StructureFamily.HORIZONTAL,
        side=ZoneSide.SUPPORT,
        timeframe_minutes=15,
        lower=99.0,
        upper=100.0,
        invalidation=98.9,
        impulse_extreme=99.0,
        formed_index=1,
        formed_time_ns=1,
        observed_time_ns=3,
        formation_indices=(1, 2),
        strength_ratio=1.5,
        source_structure_id="LEARNED_SUPPORT",
        source_pivot_span=1,
        touch_count=2,
        member_ids=("TOUCH_A", "TOUCH_B"),
    )


class ObjectiveBook:
    def target_for(self, side: Side, **_: object) -> tuple[StructureZone, float]:
        if side is not Side.LONG:
            raise AssertionError("runtime test expects a long reversal")
        target = StructureZone(
            zone_id="TARGET",
            kind=ObjectKind.HORIZONTAL_RESISTANCE,
            family=StructureFamily.HORIZONTAL,
            side=ZoneSide.RESISTANCE,
            timeframe_minutes=15,
            lower=110.0,
            upper=110.1,
            invalidation=110.2,
            impulse_extreme=110.0,
            formed_index=0,
            formed_time_ns=0,
            observed_time_ns=0,
            formation_indices=(),
            strength_ratio=1.0,
            source_structure_id="TARGET",
            source_pivot_span=1,
        )
        return target, 110.0


def make_engine() -> LearnedHorizontalScenarioEngine:
    engine = LearnedHorizontalScenarioEngine(
        "TEST",
        0.1,
        scale_name="MICRO",
        context_minutes=15,
        trigger_minutes=1,
        objective_book=ObjectiveBook(),
        minimum_gross_rr=1.0,
    )
    zone = learned_zone()
    engine.detector.zones.append(zone)
    engine.detector._zone_ids.add(zone.zone_id)
    engine.detector._active_zones[zone.zone_id] = zone
    return engine


class LearnedHorizontalRuntimeTests(unittest.TestCase):
    def test_later_full_outside_owner_terminates_ambiguous_break(self) -> None:
        engine = make_engine()
        engine.on_bar(15, candle(10, 101.0, 101.2, 98.0, 98.5))
        engine.on_bar(15, candle(20, 98.5, 100.2, 97.8, 99.4))
        self.assertIs(engine.setups[0].state, LearnedSetupState.WAITING_REENTRY)
        engine.on_bar(15, candle(30, 98.8, 99.2, 97.5, 98.2))
        self.assertIs(engine.setups[0].state, LearnedSetupState.ACCEPTED_BREAK)
        self.assertFalse(engine._active)

    def test_trap_topology_confirmation_is_idempotent(self) -> None:
        engine = make_engine()
        engine.on_bar(1, candle(9, 100.2, 100.5, 99.5, 100.1))
        engine.on_bar(15, candle(10, 101.0, 101.2, 97.8, 98.5))
        for bar in (
            candle(11, 98.5, 98.8, 97.8, 98.2),
            candle(12, 98.2, 100.2, 98.1, 99.8),
            candle(13, 99.8, 100.0, 97.9, 98.4),
            candle(14, 98.4, 98.9, 97.7, 98.1),
            candle(15, 98.1, 99.3, 98.0, 99.0),
        ):
            engine.on_bar(1, bar)
        before = engine.diagnostics.get("learned_trap_topology_confirmed", 0)
        self.assertEqual(before, 1)
        engine.on_bar(1, candle(16, 99.0, 99.5, 98.7, 99.2))
        engine.on_bar(1, candle(17, 99.2, 99.6, 98.9, 99.1))
        self.assertEqual(
            engine.diagnostics.get("learned_trap_topology_confirmed", 0),
            before,
        )


if __name__ == "__main__":
    unittest.main()
