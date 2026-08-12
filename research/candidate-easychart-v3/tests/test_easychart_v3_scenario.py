from __future__ import annotations

import unittest

from domain import Candle, Side
from easychart_mtf_scenario import MTFOverlapScenarioEngine, ScenarioPath, SetupState
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

    def support_engine(self, target_lower: float = 108.0) -> MTFOverlapScenarioEngine:
        engine = MTFOverlapScenarioEngine("BTCUSDT", 0.1)
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
        return engine

    def test_rejection_requires_later_displacement_and_later_retest(self) -> None:
        engine = self.support_engine()
        setup = engine.setups[0]

        self.assertEqual(engine.on_bar(5, self.bar(5, 102.0, 102.2, 99.0, 101.2)), [])
        self.assertEqual(setup.state, SetupState.REJECTION_WAIT_DISPLACEMENT)
        self.assertEqual(setup.path, ScenarioPath.REJECTION)

        # A later bullish engulfing OB originates in the context. It arms, but
        # cannot enter on its own formation candle.
        self.assertEqual(engine.on_bar(5, self.bar(10, 101.0, 103.2, 100.8, 103.0)), [])
        self.assertEqual(setup.state, SetupState.REJECTION_WAIT_RETEST)

        plans = engine.on_bar(5, self.bar(15, 102.5, 103.0, 101.5, 102.7))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.family, engine.REJECTION_FAMILY)
        self.assertEqual(plan.side, Side.LONG)
        self.assertAlmostEqual(plan.stop, 98.9)
        self.assertEqual(plan.target, 108.0)
        self.assertGreaterEqual(plan.gross_rr, 1.0)
        self.assertEqual(setup.state, SetupState.PLANNED)
        self.assertTrue(any("SOURCE_EXPLICIT" in rule for rule in plan.rule_provenance))

    def test_partial_excursion_is_unresolved_until_full_reclaim(self) -> None:
        engine = self.support_engine()
        setup = engine.setups[0]
        engine.on_bar(5, self.bar(5, 101.2, 101.4, 99.0, 100.4))
        self.assertEqual(setup.state, SetupState.REJECTION_WAIT_CONFIRM)
        engine.on_bar(5, self.bar(10, 100.4, 101.3, 99.2, 101.1))
        self.assertEqual(setup.state, SetupState.REJECTION_WAIT_DISPLACEMENT)
        self.assertEqual(setup.interaction_extreme, 99.0)

    def test_failed_first_retest_is_not_deferred_to_hindsight_second_retest(self) -> None:
        engine = self.support_engine()
        setup = engine.setups[0]
        engine.on_bar(5, self.bar(5, 102.0, 102.2, 99.0, 101.2))
        engine.on_bar(5, self.bar(10, 101.0, 103.2, 100.8, 103.0))
        self.assertEqual(setup.state, SetupState.REJECTION_WAIT_RETEST)
        self.assertEqual(engine.on_bar(5, self.bar(15, 102.5, 102.8, 101.3, 101.6)), [])
        self.assertEqual(setup.state, SetupState.UNRESOLVED)
        self.assertEqual(engine.on_bar(5, self.bar(20, 101.7, 103.0, 101.4, 102.9)), [])

    def test_acceptance_requires_next_decision_bar_hold_then_first_flip_retest(self) -> None:
        engine = self.support_engine(target_lower=108.0)
        setup = engine.setups[0]
        engine.on_bar(15, self.bar(15, 100.4, 100.6, 98.8, 99.2))
        self.assertEqual(setup.state, SetupState.ACCEPTANCE_WAIT_HOLD)
        engine.on_bar(15, self.bar(30, 99.1, 99.6, 97.8, 98.5))
        self.assertEqual(setup.state, SetupState.ACCEPTANCE_WAIT_RETEST)

        # Same-close 5m callback is not allowed to confirm itself.
        self.assertEqual(engine.on_bar(5, self.bar(30, 98.7, 100.2, 98.2, 99.4)), [])
        plans = engine.on_bar(5, self.bar(35, 99.4, 100.5, 98.9, 99.5))
        self.assertEqual(plans, [])
        self.assertEqual(setup.state, SetupState.TARGET_SPENT)

    def test_acceptance_short_uses_preexisting_support_target(self) -> None:
        engine = self.support_engine(target_lower=108.0)
        support_target = self.zone("D-T", ZoneKind.FVG, ZoneSide.SUPPORT, 15, 93.0, 94.0, 90.0)
        engine.detectors[15].zones.append(support_target)
        setup = engine.setups[0]
        engine.on_bar(15, self.bar(15, 100.4, 100.6, 98.8, 99.2))
        engine.on_bar(15, self.bar(30, 99.1, 99.6, 97.8, 98.5))
        engine.on_bar(5, self.bar(30, 98.7, 99.8, 98.2, 98.8))
        plans = engine.on_bar(5, self.bar(35, 99.2, 100.5, 98.9, 99.5))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.family, engine.ACCEPTANCE_FAMILY)
        self.assertEqual(plan.side, Side.SHORT)
        self.assertAlmostEqual(plan.stop, 101.1)
        self.assertEqual(plan.target, 94.0)

    def test_target_created_after_interaction_is_not_used(self) -> None:
        engine = self.support_engine(target_lower=108.0)
        setup = engine.setups[0]
        engine.on_bar(5, self.bar(5, 102.0, 102.2, 99.0, 101.2))
        original = next(z for z in engine.detectors[60].zones if z.zone_id == "H-R")
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
        engine.on_bar(5, self.bar(10, 101.0, 103.2, 100.8, 103.0))
        plans = engine.on_bar(5, self.bar(15, 102.5, 103.0, 101.5, 102.7))
        self.assertEqual(plans, [])
        self.assertEqual(setup.state, SetupState.TARGET_SPENT)

    def test_rr_below_one_consumes_first_opportunity_without_threshold_relaxation(self) -> None:
        engine = self.support_engine(target_lower=103.8)
        setup = engine.setups[0]
        engine.on_bar(5, self.bar(5, 102.0, 102.2, 99.0, 101.2))
        engine.on_bar(5, self.bar(10, 101.0, 103.2, 100.8, 103.0))
        plans = engine.on_bar(5, self.bar(15, 102.5, 103.0, 101.5, 102.7))
        self.assertEqual(plans, [])
        self.assertEqual(setup.state, SetupState.NO_TRADE_GEOMETRY)
        self.assertEqual(engine.diagnostics.get("gross_rr_below_minimum"), 1)

    def test_trace_exposes_selected_and_rejected_state_transitions(self) -> None:
        engine = self.support_engine()
        engine.on_bar(5, self.bar(5, 102.0, 102.2, 99.0, 100.4))
        kinds = [event["scenario_kind"] for event in engine.drain_trace()]
        self.assertIn("setup_created", kinds)
        self.assertIn("rejection_excursion_unresolved", kinds)


if __name__ == "__main__":
    unittest.main()
