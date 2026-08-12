from __future__ import annotations

import unittest

from domain import Candle, Side
from easychart_zones import PriceZone, ZoneKind, ZoneSide
from mtf_zone_touch_scenario import (
    MTFZoneFirstTouchScenarioEngine,
    MTFZoneTouchState,
)


class MTFZoneFirstTouchScenarioEngineTest(unittest.TestCase):
    def bar(self, minute: int, open_: float, high: float, low: float, close: float) -> Candle:
        return Candle(minute * 60_000_000_000, open_, high, low, close, 1.0)

    def zone(
        self,
        zone_id: str,
        side: ZoneSide,
        timeframe: int,
        lower: float,
        upper: float,
        invalidation: float,
        observed_minute: int,
        *,
        kind: ZoneKind = ZoneKind.ORDER_BLOCK,
        strength: float = 1.2,
    ) -> PriceZone:
        return PriceZone(
            zone_id=zone_id,
            kind=kind,
            side=side,
            timeframe_minutes=timeframe,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=upper + 1.0 if side is ZoneSide.SUPPORT else lower - 1.0,
            formed_index=0,
            formed_time_ns=(observed_minute - timeframe) * 60_000_000_000,
            observed_time_ns=observed_minute * 60_000_000_000,
            formation_indices=(0, 1),
            strength_ratio=strength,
        )

    def seeded_engine(self) -> MTFZoneFirstTouchScenarioEngine:
        engine = MTFZoneFirstTouchScenarioEngine("BTCUSDT", 0.1)
        engine.detectors[60].zones.extend(
            [
                self.zone("higher-support", ZoneSide.SUPPORT, 60, 99.0, 101.0, 96.0, 30),
                self.zone("target-resistance", ZoneSide.RESISTANCE, 60, 106.0, 107.0, 108.0, 20),
            ],
        )
        engine.detectors[15].zones.append(
            self.zone("decision-support", ZoneSide.SUPPORT, 15, 99.5, 100.5, 98.0, 50),
        )
        return engine

    def test_fresh_ob_overlap_emits_distal_edge_limit_plan_without_sweep(self) -> None:
        engine = self.seeded_engine()
        plans = engine._refresh(self.bar(60, 101.0, 103.0, 100.8, 102.0))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertIs(plan.side, Side.LONG)
        self.assertEqual(plan.family, "MTF_OB_OVERLAP_FIRST_TOUCH")
        self.assertEqual(plan.entry_order_kind, "LIMIT")
        self.assertAlmostEqual(plan.entry, 99.5)
        self.assertAlmostEqual(plan.stop, 96.0)
        self.assertAlmostEqual(plan.target, 106.0)
        self.assertGreaterEqual(plan.gross_rr, 1.0)
        self.assertNotIn("sweep", plan.causal_event_id.lower())
        self.assertEqual(plan.higher_zone_id, "higher-support")
        self.assertEqual(plan.decision_zone_id, "decision-support")

    def test_context_ob_does_not_need_arbitrary_two_x_gate(self) -> None:
        engine = self.seeded_engine()
        plans = engine._refresh(self.bar(60, 101.0, 103.0, 100.8, 102.0))
        self.assertEqual(len(plans), 1)
        self.assertLess(plans[0].higher_strength_ratio, 2.0)
        self.assertLess(plans[0].decision_strength_ratio, 2.0)

    def test_already_touched_source_is_not_reused_as_fresh_limit(self) -> None:
        engine = self.seeded_engine()
        engine.detectors[15].zones[0].first_touch_index = 3
        engine.detectors[15].zones[0].first_touch_time_ns = 55 * 60_000_000_000
        plans = engine._refresh(self.bar(60, 101.0, 103.0, 100.8, 102.0))
        self.assertEqual(plans, [])
        self.assertEqual(engine.setups, [])

    def test_fvg_overlap_is_not_silently_called_the_case_28_ob_family(self) -> None:
        engine = self.seeded_engine()
        engine.detectors[15].zones[0].kind = ZoneKind.FVG
        plans = engine._refresh(self.bar(60, 101.0, 103.0, 100.8, 102.0))
        self.assertEqual(plans, [])

    def test_nearest_low_rr_target_blocks_skipping_to_farther_zone(self) -> None:
        engine = self.seeded_engine()
        engine.detectors[60].zones[1] = self.zone(
            "near-resistance",
            ZoneSide.RESISTANCE,
            60,
            101.0,
            102.0,
            103.0,
            20,
        )
        engine.detectors[60].zones.append(
            self.zone(
                "far-resistance",
                ZoneSide.RESISTANCE,
                60,
                108.0,
                109.0,
                110.0,
                10,
            ),
        )
        plans = engine._refresh(self.bar(60, 99.7, 100.0, 99.6, 99.8))
        self.assertEqual(plans, [])
        self.assertEqual(len(engine.setups), 1)
        self.assertIs(engine.setups[0].state, MTFZoneTouchState.RR_BELOW_MINIMUM)
        self.assertEqual(engine.diagnostics.get("setup_rr_below_minimum"), 1)

    def test_target_already_traded_inside_observation_bar_is_not_future_space(self) -> None:
        engine = self.seeded_engine()
        plans = engine._refresh(self.bar(60, 101.0, 106.2, 100.8, 102.0))
        self.assertEqual(plans, [])
        self.assertIs(engine.setups[0].state, MTFZoneTouchState.NO_OBJECTIVE)


if __name__ == "__main__":
    unittest.main()
