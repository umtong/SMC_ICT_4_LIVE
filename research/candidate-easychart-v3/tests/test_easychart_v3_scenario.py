from __future__ import annotations

import unittest

from domain import Candle, Side
from easychart_mtf_scenario import ScaleScenarioEngine, ScenarioPath, SetupState
from easychart_zones import PriceZone, ZoneKind, ZoneSide


class EasyChartV3ScenarioTest(unittest.TestCase):
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
        observed_minute: int = 0,
        strength: float = 2.5,
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
            formed_time_ns=observed_minute * self.NS,
            observed_time_ns=observed_minute * self.NS,
            formation_indices=(0,),
            strength_ratio=strength,
        )

    def support_engine(self, target_lower: float = 108.0) -> ScaleScenarioEngine:
        engine = ScaleScenarioEngine("BTCUSDT", 0.1)
        higher = self.zone("H-S", ZoneKind.FVG, ZoneSide.SUPPORT, 60, 99.5, 102.0, 95.0)
        decision = self.zone("D-S", ZoneKind.ORDER_BLOCK, ZoneSide.SUPPORT, 15, 100.0, 101.0, 96.0)
        target = self.zone(
            "H-R",
            ZoneKind.ORDER_BLOCK,
            ZoneSide.RESISTANCE,
            60,
            target_lower,
            target_lower + 1.0,
            target_lower + 3.0,
        )
        engine.detectors[60].zones.extend([higher, target])
        engine.detectors[15].zones.append(decision)
        engine._refresh_setups(self.NS)
        self.assertEqual(len(engine.setups), 1)
        engine.on_bar(5, self.bar(1, 102.1, 102.4, 101.8, 102.2))
        return engine

    def test_rejection_requires_later_source_sized_displacement_and_later_retest(self) -> None:
        engine = self.support_engine()
        setup = engine.setups[0]
        self.assertEqual(engine.on_bar(5, self.bar(5, 102.0, 102.2, 99.0, 101.2)), [])
        self.assertEqual(setup.state, SetupState.WAITING_DISPLACEMENT)
        self.assertEqual(setup.path, ScenarioPath.REJECTION)
        self.assertEqual(engine.on_bar(5, self.bar(10, 101.1, 103.3, 100.8, 103.2)), [])
        self.assertEqual(setup.state, SetupState.WAITING_RETEST)
        plans = engine.on_bar(5, self.bar(15, 102.5, 103.0, 101.5, 102.7))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.side, Side.LONG)
        self.assertEqual(plan.scale_name, "MACRO")
        self.assertEqual(plan.scenario_path, "REJECTION")
        self.assertAlmostEqual(plan.stop, 98.9)
        self.assertEqual(plan.target, 108.0)
        self.assertGreaterEqual(plan.gross_rr, 1.0)

    def test_ordinary_context_touch_is_distinct_from_sweep(self) -> None:
        engine = self.support_engine()
        setup = engine.setups[0]
        engine.on_bar(5, self.bar(5, 102.0, 102.2, 100.4, 101.4))
        self.assertEqual(setup.state, SetupState.WAITING_DISPLACEMENT)
        self.assertEqual(setup.path, ScenarioPath.TOUCH)
        engine.on_bar(5, self.bar(10, 101.3, 103.5, 100.9, 103.3))
        self.assertEqual(setup.state, SetupState.WAITING_RETEST)
        plans = engine.on_bar(5, self.bar(15, 102.4, 103.0, 101.5, 102.8))
        self.assertEqual(len(plans), 1)
        self.assertIn("CONFLUENCE_TOUCH", plans[0].family)

    def test_sub_two_x_order_block_is_not_a_trigger(self) -> None:
        engine = self.support_engine()
        setup = engine.setups[0]
        engine.on_bar(5, self.bar(5, 102.0, 102.2, 99.0, 101.2))
        engine.on_bar(5, self.bar(10, 101.1, 102.2, 100.8, 102.1))
        self.assertEqual(setup.state, SetupState.WAITING_DISPLACEMENT)

    def test_first_failed_retest_is_not_replaced_by_prettier_second_retest(self) -> None:
        engine = self.support_engine()
        setup = engine.setups[0]
        engine.on_bar(5, self.bar(5, 102.0, 102.2, 99.0, 101.2))
        engine.on_bar(5, self.bar(10, 101.1, 103.3, 100.8, 103.2))
        engine.on_bar(5, self.bar(15, 102.5, 102.8, 101.3, 101.6))
        self.assertEqual(setup.state, SetupState.UNRESOLVED)
        self.assertEqual(engine.on_bar(5, self.bar(20, 101.7, 103.0, 101.4, 102.9)), [])

    def test_target_created_after_interaction_is_not_used(self) -> None:
        engine = self.support_engine()
        setup = engine.setups[0]
        engine.on_bar(5, self.bar(5, 102.0, 102.2, 99.0, 101.2))
        original = next(zone for zone in engine.detectors[60].zones if zone.zone_id == "H-R")
        original.consumed = True
        late = self.zone(
            "LATE-R",
            ZoneKind.FVG,
            ZoneSide.RESISTANCE,
            15,
            108.0,
            109.0,
            112.0,
            observed_minute=7,
        )
        engine.detectors[15].zones.append(late)
        engine.on_bar(5, self.bar(10, 101.1, 103.3, 100.8, 103.2))
        self.assertEqual(engine.on_bar(5, self.bar(15, 102.5, 103.0, 101.5, 102.7)), [])
        self.assertEqual(setup.state, SetupState.TARGET_SPENT)

    def test_rr_below_one_consumes_opportunity_without_relaxation(self) -> None:
        engine = self.support_engine(target_lower=103.8)
        setup = engine.setups[0]
        engine.on_bar(5, self.bar(5, 102.0, 102.2, 99.0, 101.2))
        engine.on_bar(5, self.bar(10, 101.1, 103.3, 100.8, 103.2))
        self.assertEqual(engine.on_bar(5, self.bar(15, 102.5, 103.0, 101.5, 102.7)), [])
        self.assertEqual(setup.state, SetupState.NO_TRADE_GEOMETRY)
        self.assertEqual(engine.diagnostics.get("gross_rr_below_minimum"), 1)


if __name__ == "__main__":
    unittest.main()
