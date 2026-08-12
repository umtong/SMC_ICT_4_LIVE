from __future__ import annotations

import unittest

from domain import Candle, Side
from easychart_zones import PriceZone, ZoneKind, ZoneSide
from liquidity import ObjectiveKind, ObjectiveZone
from scenario_bundle_v3 import (
    ActiveEasyChartZoneDetector,
    HorizontalState,
    HorizontalSweepScenarioEngine,
)


class ScenarioBundleV3Test(unittest.TestCase):
    NS = 60_000_000_000

    def bar(self, minute: int, open_: float, high: float, low: float, close: float) -> Candle:
        return Candle(minute * self.NS, open_, high, low, close, 1.0)

    def objective(self, zone_id: str, kind: ObjectiveKind, level: float) -> ObjectiveZone:
        side = ZoneSide.RESISTANCE if kind is ObjectiveKind.SWING_HIGH else ZoneSide.SUPPORT
        return ObjectiveZone(
            zone_id=zone_id,
            kind=kind,
            side=side,
            timeframe_minutes=15,
            lower=level if side is ZoneSide.RESISTANCE else level - 0.1,
            upper=level + 0.1 if side is ZoneSide.RESISTANCE else level,
            invalidation=level + 0.2 if side is ZoneSide.RESISTANCE else level - 0.2,
            impulse_extreme=level,
            formed_index=0,
            formed_time_ns=0,
            observed_time_ns=0,
            formation_indices=(0,),
            strength_ratio=2.0,
            pivot_span=6,
        )

    def horizontal_engine(self) -> HorizontalSweepScenarioEngine:
        engine = HorizontalSweepScenarioEngine(
            "BTCUSDT",
            0.1,
            scale_name="MICRO_HORIZONTAL",
            context_minutes=15,
            trigger_minutes=1,
            minimum_gross_rr=1.0,
        )
        support = self.objective("LOW", ObjectiveKind.SWING_LOW, 100.0)
        resistance = self.objective("HIGH", ObjectiveKind.SWING_HIGH, 110.0)
        engine.level_detector.zones.extend([support, resistance])
        engine.level_detector._active[support.zone_id] = support
        engine.level_detector._active[resistance.zone_id] = resistance
        engine._create_setups([support, resistance])
        return engine

    def test_horizontal_sweep_reclaim_requires_later_displacement_and_retest(self) -> None:
        engine = self.horizontal_engine()
        setup = next(item for item in engine.setups if item.level.zone_id == "LOW")
        engine.on_bar(1, self.bar(1, 101.2, 101.4, 100.8, 101.0))
        engine.on_bar(1, self.bar(2, 101.0, 101.2, 99.0, 100.5))
        self.assertEqual(setup.state, HorizontalState.WAITING_DISPLACEMENT)
        engine.on_bar(1, self.bar(3, 100.4, 102.2, 100.2, 102.0))
        self.assertEqual(setup.state, HorizontalState.WAITING_RETEST)
        plans = engine.on_bar(1, self.bar(4, 101.2, 101.6, 100.7, 101.4))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.side, Side.LONG)
        self.assertEqual(plan.target_zone_id, "HIGH")
        self.assertEqual(plan.target, 110.0)
        self.assertAlmostEqual(plan.stop, 98.9)
        self.assertGreaterEqual(plan.gross_rr, 1.0)

    def test_active_zone_detector_preserves_archive_and_removes_dead_zone_from_hot_path(self) -> None:
        detector = ActiveEasyChartZoneDetector("BTCUSDT", 1, 0.1)
        zone = PriceZone(
            zone_id="Z",
            kind=ZoneKind.ORDER_BLOCK,
            side=ZoneSide.SUPPORT,
            timeframe_minutes=1,
            lower=100.0,
            upper=101.0,
            invalidation=99.0,
            impulse_extreme=102.0,
            formed_index=0,
            formed_time_ns=0,
            observed_time_ns=0,
            formation_indices=(0,),
            strength_ratio=2.0,
        )
        detector.zones.append(zone)
        detector.bars.append(self.bar(1, 101.0, 101.1, 100.5, 100.8))
        self.assertEqual([item.zone_id for item in detector.active_zones()], ["Z"])
        detector.on_bar(self.bar(2, 100.0, 100.2, 98.9, 99.2))
        self.assertEqual(detector.active_zones(), [])
        self.assertEqual(len(detector.zones), 1)
        self.assertIsNotNone(zone.invalidated_time_ns)


if __name__ == "__main__":
    unittest.main()
