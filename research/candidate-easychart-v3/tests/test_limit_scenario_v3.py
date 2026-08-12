from __future__ import annotations

import unittest

from domain import Candle, Side
from easychart_mtf_scenario import SetupState
from easychart_zones import PriceZone, ZoneKind, ZoneSide
from limit_scenario_v3 import LimitScaleScenarioEngine


class LimitScenarioV3Test(unittest.TestCase):
    NS = 60_000_000_000

    def bar(self, minute: int, open_: float, high: float, low: float, close: float) -> Candle:
        return Candle(minute * self.NS, open_, high, low, close, 1.0)

    def zone(
        self,
        zone_id: str,
        kind: ZoneKind,
        side: ZoneSide,
        timeframe: int,
        lower: float,
        upper: float,
        invalidation: float,
    ) -> PriceZone:
        return PriceZone(
            zone_id=zone_id,
            kind=kind,
            side=side,
            timeframe_minutes=timeframe,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=upper if side is ZoneSide.SUPPORT else lower,
            formed_index=0,
            formed_time_ns=0,
            observed_time_ns=0,
            formation_indices=(0,),
            strength_ratio=2.5,
        )

    def engine(self) -> LimitScaleScenarioEngine:
        engine = LimitScaleScenarioEngine("BTCUSDT", 0.1)
        support_60 = self.zone("H-S", ZoneKind.FVG, ZoneSide.SUPPORT, 60, 99.5, 102.0, 95.0)
        support_15 = self.zone("D-S", ZoneKind.ORDER_BLOCK, ZoneSide.SUPPORT, 15, 100.0, 101.0, 96.0)
        target = self.zone("H-R", ZoneKind.ORDER_BLOCK, ZoneSide.RESISTANCE, 60, 108.0, 109.0, 112.0)
        engine.detectors[60].zones.extend([support_60, target])
        engine.detectors[15].zones.append(support_15)
        engine._refresh_setups(self.NS)
        engine.on_bar(5, self.bar(1, 102.1, 102.4, 101.8, 102.2))
        return engine

    def test_plan_is_emitted_at_displacement_with_proximal_limit_not_after_retest(self) -> None:
        engine = self.engine()
        setup = engine.setups[0]
        engine.on_bar(5, self.bar(5, 102.0, 102.2, 99.0, 101.2))
        self.assertEqual(setup.state, SetupState.WAITING_DISPLACEMENT)
        plans = engine.on_bar(5, self.bar(10, 101.1, 103.3, 100.8, 103.2))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.side, Side.LONG)
        self.assertAlmostEqual(plan.entry, 102.0)
        self.assertAlmostEqual(plan.stop, 98.9)
        self.assertEqual(plan.target, 108.0)
        self.assertGreaterEqual(plan.gross_rr, 1.0)
        self.assertLess(plan.entry, 103.2)
        self.assertEqual(setup.state, SetupState.PLANNED)


if __name__ == "__main__":
    unittest.main()
