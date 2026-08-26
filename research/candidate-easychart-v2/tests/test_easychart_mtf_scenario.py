from __future__ import annotations

import unittest

from domain import Candle, Side
from easychart_mtf_scenario import MTFOverlapScenarioEngine, SetupState
from easychart_zones import PriceZone, ZoneKind, ZoneSide


class MTFOverlapScenarioEngineTest(unittest.TestCase):
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
            impulse_extreme=upper + 1.0 if side is ZoneSide.SUPPORT else lower - 1.0,
            formed_index=0,
            formed_time_ns=(observed_minute - timeframe) * 60_000_000_000,
            observed_time_ns=observed_minute * 60_000_000_000,
            formation_indices=(0, 1),
            strength_ratio=strength,
        )

    def seeded_engine(
        self,
        *,
        target_observed_minute: int = 20,
        target_lower: float = 106.0,
    ) -> MTFOverlapScenarioEngine:
        engine = MTFOverlapScenarioEngine("BTCUSDT", 0.1)
        higher = self.zone(
            "higher-support",
            ZoneSide.SUPPORT,
            60,
            99.0,
            100.0,
            97.0,
            30,
        )
        lower = self.zone(
            "lower-support",
            ZoneSide.SUPPORT,
            15,
            99.2,
            100.2,
            98.5,
            50,
        )
        target = self.zone(
            "target-resistance",
            ZoneSide.RESISTANCE,
            60,
            target_lower,
            target_lower + 1.0,
            target_lower + 2.0,
            target_observed_minute,
        )
        engine.detectors[60].zones.extend([higher, target])
        engine.detectors[15].zones.append(lower)
        engine._refresh_setups()
        return engine

    def test_context_interaction_and_entry_confirmation_are_distinct_observations(self) -> None:
        engine = self.seeded_engine()
        self.assertEqual(len(engine.setups), 1)
        setup = engine.setups[0]
        self.assertIs(setup.state, SetupState.WAITING_INTERACTION)

        plans = engine.on_bar(5, self.bar(60, 99.8, 100.2, 98.8, 99.2))
        self.assertEqual(plans, [])
        self.assertIs(setup.state, SetupState.WAITING_TRIGGER)
        self.assertEqual(setup.interaction_time_ns, self.bar(60, 99.8, 100.2, 98.8, 99.2).ts_close_ns)

        plans = engine.on_bar(5, self.bar(65, 99.0, 101.5, 98.7, 101.0))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertIs(plan.side, Side.LONG)
        self.assertEqual(plan.entry, 101.0)
        self.assertAlmostEqual(plan.stop, 98.6)
        self.assertEqual(plan.target, 106.0)
        self.assertGreaterEqual(plan.gross_rr, 1.0)
        self.assertEqual(plan.higher_zone_id, "higher-support")
        self.assertEqual(plan.lower_zone_id, "lower-support")
        self.assertGreaterEqual(plan.trigger_strength_ratio, 2.0)
        self.assertIs(setup.state, SetupState.PLANNED)

    def test_opposite_target_must_exist_before_entry_confirmation(self) -> None:
        engine = self.seeded_engine(target_observed_minute=70)
        engine.on_bar(5, self.bar(60, 99.8, 100.2, 98.8, 99.2))
        plans = engine.on_bar(5, self.bar(65, 99.0, 101.5, 98.7, 101.0))
        self.assertEqual(plans, [])
        self.assertEqual(engine.diagnostics.get("trigger_without_unspent_preexisting_target"), 1)

    def test_target_touched_inside_confirmation_bar_is_not_future_space(self) -> None:
        engine = self.seeded_engine(target_lower=101.4)
        engine.on_bar(5, self.bar(60, 99.8, 100.2, 98.8, 99.2))
        # The high reaches the target before this candle closes. A close-based
        # strategy cannot enter afterward and count the already printed target.
        plans = engine.on_bar(5, self.bar(65, 99.0, 101.5, 98.7, 101.0))
        self.assertEqual(plans, [])
        self.assertEqual(engine.diagnostics.get("trigger_without_unspent_preexisting_target"), 1)

    def test_source_zone_exact_invalidation_touch_ends_setup_before_trigger(self) -> None:
        engine = self.seeded_engine()
        setup = engine.setups[0]
        engine.on_bar(5, self.bar(60, 99.8, 100.2, 98.8, 99.2))
        plans = engine.on_bar(5, self.bar(65, 99.2, 100.0, 97.0, 97.5))
        self.assertEqual(plans, [])
        self.assertIs(setup.state, SetupState.INVALIDATED)

    def test_two_marginal_engulfing_zones_do_not_become_strong_by_overlap_alone(self) -> None:
        engine = MTFOverlapScenarioEngine("BTCUSDT", 0.1)
        higher = self.zone(
            "higher-marginal",
            ZoneSide.SUPPORT,
            60,
            99.0,
            100.0,
            97.0,
            30,
            strength=1.2,
        )
        lower = self.zone(
            "lower-marginal",
            ZoneSide.SUPPORT,
            15,
            99.2,
            100.2,
            98.5,
            50,
            strength=1.5,
        )
        engine.detectors[60].zones.append(higher)
        engine.detectors[15].zones.append(lower)
        engine._refresh_setups()
        self.assertEqual(engine.setups, [])

    def test_fvg_and_order_block_can_supply_different_roles_in_one_overlap(self) -> None:
        engine = MTFOverlapScenarioEngine("BTCUSDT", 0.1)
        higher_fvg = self.zone(
            "higher-fvg",
            ZoneSide.SUPPORT,
            60,
            98.5,
            100.0,
            96.0,
            30,
            kind=ZoneKind.FVG,
            strength=3.0,
        )
        lower_ob = self.zone(
            "lower-ob",
            ZoneSide.SUPPORT,
            15,
            99.0,
            100.5,
            98.0,
            50,
            kind=ZoneKind.ORDER_BLOCK,
            strength=1.3,
        )
        engine.detectors[60].zones.append(higher_fvg)
        engine.detectors[15].zones.append(lower_ob)
        engine._refresh_setups()
        self.assertEqual(len(engine.setups), 1)
        setup = engine.setups[0]
        self.assertIs(setup.higher_zone.kind, ZoneKind.FVG)
        self.assertIs(setup.lower_zone.kind, ZoneKind.ORDER_BLOCK)
        self.assertEqual((setup.overlap.lower, setup.overlap.upper), (99.0, 100.0))


if __name__ == "__main__":
    unittest.main()
