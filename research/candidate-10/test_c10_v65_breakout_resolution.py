from __future__ import annotations

import unittest

from c10_v65_breakout_resolution import BreakoutResolutionAuctionEngine
from logic import (
    BarObs,
    Direction,
    LogicConfig,
    Scenario,
    StructuralBar,
)


class BreakoutResolutionAuctionEngineTest(unittest.TestCase):
    @staticmethod
    def engine() -> BreakoutResolutionAuctionEngine:
        engine = BreakoutResolutionAuctionEngine(
            LogicConfig(),
            "BTCUSDT-PERP.BINANCE",
        )
        engine.bars = [
            BarObs(60_000_000_000, 99.0, 100.0, 98.5, 99.5, 100.0, 55.0),
            BarObs(120_000_000_000, 99.5, 100.2, 99.0, 100.0, 100.0, 55.0),
            BarObs(180_000_000_000, 100.0, 102.5, 99.8, 101.8, 150.0, 105.0),
        ]
        engine._index = 2
        engine.context_bars = [
            StructuralBar(
                1, 2, 98.0, 101.0, 97.0, 100.0,
                1000.0, 520.0, 1, 1,
            ),
            StructuralBar(
                3, 4, 100.0, 104.0, 99.0, 103.0,
                1200.0, 720.0, 3, 3,
            ),
        ]
        return engine

    @staticmethod
    def arm_long(engine: BreakoutResolutionAuctionEngine) -> None:
        engine._arm_episode(
            bar=engine.bars[-1],
            direction=Direction.LONG,
            breakout=(60_000_000_000, 120_000_000_000, 100.2),
            relative_volume=1.5,
        )

    def test_breakout_arms_without_immediate_trade(self) -> None:
        engine = self.engine()
        self.arm_long(engine)
        self.assertIsNotNone(engine.breakout_episode)
        self.assertIsNone(engine.pending_plan_id)
        self.assertEqual(engine.events[-1].event_type, "BREAKOUT_EPISODE_ARMED")
        self.assertEqual(engine.events[-1].next_state, "OBSERVE")

    def test_later_retest_survival_emits_continuation(self) -> None:
        engine = self.engine()
        self.arm_long(engine)
        assert engine.breakout_episode is not None
        boundary = engine.breakout_episode.boundary
        engine.internal_highs = [
            (1, 120_000_000_000, boundary + 5.0),
        ]
        bar = BarObs(
            240_000_000_000,
            boundary + 0.4,
            boundary + 1.0,
            boundary - 0.2,
            boundary + 0.7,
            100.0,
            60.0,
        )
        plan = engine._advance_episode(
            bar=bar,
            atr=1.0,
            relative_volume=1.0,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, Scenario.AAC)
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertEqual(plan.expected_entry, boundary)
        self.assertLess(plan.stop_price, bar.low)
        self.assertGreater(plan.target_price, bar.high)
        self.assertEqual(plan.details["resolution"], "RETEST_CONTINUATION")
        self.assertEqual(
            plan.details["sweep_ts_ns"],
            120_000_000_000,
        )

    def test_close_back_through_boundary_emits_failed_breakout_reversal(self) -> None:
        engine = self.engine()
        self.arm_long(engine)
        assert engine.breakout_episode is not None
        boundary = engine.breakout_episode.boundary
        engine.internal_lows = [
            (1, 120_000_000_000, boundary - 6.0),
        ]
        bar = BarObs(
            240_000_000_000,
            boundary + 0.2,
            boundary + 0.5,
            boundary - 1.2,
            boundary - 0.8,
            100.0,
            30.0,
        )
        plan = engine._advance_episode(
            bar=bar,
            atr=1.0,
            relative_volume=1.0,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.scenario, Scenario.FAR)
        self.assertEqual(plan.direction, Direction.SHORT)
        self.assertEqual(plan.expected_entry, boundary)
        self.assertGreater(plan.stop_price, engine.bars[-1].high)
        self.assertLess(plan.target_price, bar.low)
        self.assertEqual(
            plan.details["resolution"],
            "FAILED_ACCEPTANCE_REVERSAL",
        )

    def test_unresolved_episode_expires_without_trade(self) -> None:
        engine = self.engine()
        self.arm_long(engine)
        assert engine.breakout_episode is not None
        expiry = engine.breakout_episode.expire_ts_ns
        bar = BarObs(
            expiry + 60_000_000_000,
            101.0,
            101.5,
            100.5,
            101.0,
            100.0,
            50.0,
        )
        plan = engine._advance_episode(
            bar=bar,
            atr=1.0,
            relative_volume=1.0,
        )
        self.assertIsNone(plan)
        self.assertIsNone(engine.breakout_episode)
        self.assertIsNone(engine.pending_plan_id)
        self.assertEqual(
            engine.events[-1].reason_code,
            "BREAKOUT_RESOLUTION_WINDOW_EXPIRED",
        )
        self.assertEqual(engine.events[-1].next_state, "TERMINAL")

    def test_resolved_plan_keeps_entry_stop_target_in_one_leg(self) -> None:
        engine = self.engine()
        self.arm_long(engine)
        assert engine.breakout_episode is not None
        boundary = engine.breakout_episode.boundary
        engine.internal_highs = [
            (1, 120_000_000_000, boundary + 7.0),
        ]
        bar = BarObs(
            240_000_000_000,
            boundary + 0.2,
            boundary + 0.8,
            boundary - 0.4,
            boundary + 0.6,
            100.0,
            60.0,
        )
        plan = engine._advance_episode(
            bar=bar,
            atr=1.0,
            relative_volume=1.0,
        )
        assert plan is not None
        self.assertLess(plan.stop_price, plan.expected_entry)
        self.assertGreater(plan.target_price, plan.expected_entry)
        self.assertGreaterEqual(plan.net_r, engine.config.min_net_r)
        self.assertEqual(plan.details["position_horizon_minutes"], 240)
        self.assertEqual(engine.pending_plan_id, plan.scenario_id)


if __name__ == "__main__":
    unittest.main()
