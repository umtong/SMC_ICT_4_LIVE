from __future__ import annotations

import unittest

from causal_swings import SwingPoint, SwingSide
from domain import Candle, Side
from easychart_mtf_scenario import MTFOverlapScenarioEngine, SetupState
from easychart_zones import PriceZone, ZoneKind, ZoneSide


class MTFOverlapScenarioEngineTest(unittest.TestCase):
    NS_PER_MINUTE = 60_000_000_000

    def bar(self, minute: int, open_: float, high: float, low: float, close: float) -> Candle:
        return Candle(minute * self.NS_PER_MINUTE, open_, high, low, close, 1.0)

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
            formed_time_ns=(observed_minute - timeframe) * self.NS_PER_MINUTE,
            observed_time_ns=observed_minute * self.NS_PER_MINUTE,
            formation_indices=(0, 1),
            strength_ratio=strength,
        )

    def seed_low_liquidity(self, engine: MTFOverlapScenarioEngine, level: float = 99.6) -> None:
        engine.swing_tracker.swings.append(
            SwingPoint(
                swing_id="local-low",
                side=SwingSide.LOW,
                level=level,
                event_index=0,
                observed_index=2,
                event_time_ns=45 * self.NS_PER_MINUTE,
                observed_time_ns=55 * self.NS_PER_MINUTE,
                span=2,
            ),
        )

    def seeded_engine(
        self,
        *,
        target_observed_minute: int = 20,
        target_lower: float = 106.0,
        target_touched: bool = False,
        with_liquidity: bool = True,
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
        if target_touched:
            target.first_touch_index = 2
            target.first_touch_time_ns = 25 * self.NS_PER_MINUTE
        engine.detectors[60].zones.extend([higher, target])
        engine.detectors[15].zones.append(lower)
        engine._refresh_setups()
        if with_liquidity:
            self.seed_low_liquidity(engine)
        return engine

    def sweep(self, engine: MTFOverlapScenarioEngine) -> None:
        plans = engine.on_bar(5, self.bar(60, 99.8, 100.2, 98.8, 99.2))
        self.assertEqual(plans, [])

    def test_sweep_and_first_later_order_block_are_distinct_observations(self) -> None:
        engine = self.seeded_engine()
        setup = engine.setups[0]
        self.assertIs(setup.state, SetupState.WAITING_LIQUIDITY_EVENT)

        self.sweep(engine)
        self.assertIs(setup.state, SetupState.WAITING_TRIGGER)
        self.assertEqual(setup.liquidity_swing_id, "local-low")
        self.assertEqual(setup.liquidity_swing_level, 99.6)
        self.assertEqual(setup.sweep_extreme, 98.8)

        plans = engine.on_bar(5, self.bar(65, 99.0, 101.5, 98.7, 101.0))
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertIs(plan.side, Side.LONG)
        self.assertEqual(plan.entry, 101.0)
        self.assertAlmostEqual(plan.stop, 98.6)
        self.assertEqual(plan.target, 106.0)
        self.assertEqual(plan.liquidity_swing_id, "local-low")
        self.assertEqual(plan.interaction_time_ns, 60 * self.NS_PER_MINUTE)
        self.assertEqual(plan.trigger_time_ns, 65 * self.NS_PER_MINUTE)
        self.assertGreaterEqual(plan.trigger_strength_ratio, 2.0)
        self.assertIs(setup.state, SetupState.PLANNED)

    def test_zone_contact_without_observable_swing_is_no_trade(self) -> None:
        engine = self.seeded_engine(with_liquidity=False)
        setup = engine.setups[0]
        plans = engine.on_bar(5, self.bar(60, 99.8, 100.3, 98.8, 100.25))
        self.assertEqual(plans, [])
        self.assertIs(setup.state, SetupState.MISSED_WITHOUT_LIQUIDITY)
        self.assertEqual(engine.diagnostics.get("setup_missed_without_liquidity_sweep"), 1)

    def test_first_weak_order_block_ends_episode_instead_of_selecting_later_one(self) -> None:
        engine = self.seeded_engine()
        setup = engine.setups[0]
        self.sweep(engine)
        # Previous body = 0.6, current bullish body = 1.15: a valid engulfing OB
        # but <2x, so it is the weak first OB and permanently ends this episode.
        plans = engine.on_bar(5, self.bar(65, 99.1, 101.0, 98.9, 100.25))
        self.assertEqual(plans, [])
        self.assertIs(setup.state, SetupState.MISSED_WEAK_FIRST_TRIGGER)
        self.assertEqual(engine.diagnostics.get("first_trigger_order_block_below_two_x"), 1)
        later = engine.on_bar(5, self.bar(70, 99.0, 102.0, 98.8, 101.5))
        self.assertEqual(later, [])

    def test_target_must_be_fresh_preexisting_and_unspent(self) -> None:
        for kwargs in (
            {"target_observed_minute": 70},
            {"target_touched": True},
            {"target_lower": 101.4},
        ):
            with self.subTest(**kwargs):
                engine = self.seeded_engine(**kwargs)
                self.sweep(engine)
                plans = engine.on_bar(5, self.bar(65, 99.0, 101.5, 98.7, 101.0))
                self.assertEqual(plans, [])
                self.assertEqual(engine.diagnostics.get("trigger_without_fresh_preexisting_target"), 1)

    def test_exact_source_invalidation_ends_setup(self) -> None:
        engine = self.seeded_engine()
        setup = engine.setups[0]
        self.sweep(engine)
        plans = engine.on_bar(5, self.bar(65, 99.2, 100.0, 97.0, 97.5))
        self.assertEqual(plans, [])
        self.assertIs(setup.state, SetupState.INVALIDATED)

    def test_fvg_only_overlap_is_not_a_complete_trade_reason(self) -> None:
        engine = MTFOverlapScenarioEngine("BTCUSDT", 0.1)
        higher = self.zone(
            "higher-fvg", ZoneSide.SUPPORT, 60, 98.5, 100.0, 96.0, 30,
            kind=ZoneKind.FVG, strength=3.0,
        )
        lower = self.zone(
            "lower-fvg", ZoneSide.SUPPORT, 15, 99.0, 100.5, 98.0, 50,
            kind=ZoneKind.FVG, strength=3.0,
        )
        engine.detectors[60].zones.append(higher)
        engine.detectors[15].zones.append(lower)
        engine._refresh_setups()
        self.assertEqual(engine.setups, [])
        self.assertEqual(engine.diagnostics.get("setup_rejected_fvg_only_context"), 1)

    def test_fvg_and_order_block_can_supply_different_context_roles(self) -> None:
        engine = MTFOverlapScenarioEngine("BTCUSDT", 0.1)
        higher_fvg = self.zone(
            "higher-fvg", ZoneSide.SUPPORT, 60, 98.5, 100.0, 96.0, 30,
            kind=ZoneKind.FVG, strength=3.0,
        )
        lower_ob = self.zone(
            "lower-ob", ZoneSide.SUPPORT, 15, 99.0, 100.5, 98.0, 50,
            kind=ZoneKind.ORDER_BLOCK, strength=1.3,
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
