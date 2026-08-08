from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

from c10_v64_intraday_delivery import (
    InternalLiquidityTarget,
    IntradayDeliveryContinuationEngine,
)
from c10_v64_overlay import resolve_intraday_acceptance
from logic import BarObs, Direction, LogicConfig, Side, StructuralBar


class IntradayDeliveryEngineTest(unittest.TestCase):
    @staticmethod
    def engine() -> IntradayDeliveryContinuationEngine:
        engine = IntradayDeliveryContinuationEngine(
            LogicConfig(),
            "BTCUSDT-PERP.BINANCE",
        )
        engine.bars = [
            BarObs(60_000_000_000, 99.0, 100.0, 98.5, 99.5, 100.0, 55.0),
            BarObs(120_000_000_000, 99.5, 100.2, 99.0, 100.0, 100.0, 55.0),
            BarObs(180_000_000_000, 101.0, 102.5, 100.5, 102.0, 150.0, 105.0),
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
    def target() -> InternalLiquidityTarget:
        return InternalLiquidityTarget(
            scenario_id="BTC-INTERNAL-HIGH-1-2",
            side=Side.HIGH,
            level=106.0,
            source="CONFIRMED_5M_INTERNAL_LIQUIDITY",
            event_ts_ns=1,
            confirmed_ts_ns=2,
        )

    def test_plan_uses_same_leg_prices_and_four_hour_horizon(self) -> None:
        engine = self.engine()
        context = engine._completed_context_state(Direction.LONG)
        self.assertTrue(context["aligned"])
        plan = engine._build_plan(
            bar=engine.bars[-1],
            prev=engine.bars[-2],
            atr=1.0,
            direction=Direction.LONG,
            breakout=(60_000_000_000, 120_000_000_000, 100.2),
            target=self.target(),
            relative_volume=1.5,
            context=context,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertGreaterEqual(plan.expected_entry, 100.2)
        self.assertLess(plan.stop_price, engine.bars[-1].low)
        self.assertGreater(plan.target_price, engine.bars[-1].high)
        self.assertGreaterEqual(plan.net_r, engine.config.min_net_r)
        self.assertEqual(
            plan.details["sweep_ts_ns"],
            120_000_000_000,
        )
        self.assertEqual(
            plan.details["position_expire_ts_ns"],
            plan.observed_ts_ns + 240 * 60_000_000_000,
        )

    def test_internal_target_is_retired_after_post_confirmation_touch(self) -> None:
        engine = self.engine()
        point = (1, 120_000_000_000, 106.0)
        engine.internal_highs = [point]
        target_id = engine._internal_target_id(Side.HIGH, point)
        engine._consume_internal_first_passage(
            BarObs(
                240_000_000_000,
                102.0,
                106.1,
                101.0,
                105.5,
                100.0,
                60.0,
            ),
        )
        self.assertIn(target_id, engine.consumed_internal_target_ids)

    def test_nearest_target_is_live_known_and_unused(self) -> None:
        engine = self.engine()
        engine.internal_highs = [
            (1, 120_000_000_000, 102.4),
            (2, 120_000_000_000, 106.0),
            (3, 120_000_000_000, 108.0),
        ]
        target = engine._nearest_internal_target(Direction.LONG, engine.bars[-1])
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.level, 106.0)
        engine.used_target_ids.add(target.scenario_id)
        second = engine._nearest_internal_target(Direction.LONG, engine.bars[-1])
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.level, 108.0)


class IntradayAcceptanceRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get("C10_V64_RESOLVED_ACCEPTANCE_ONLY")
        os.environ["C10_V64_RESOLVED_ACCEPTANCE_ONLY"] = "1"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("C10_V64_RESOLVED_ACCEPTANCE_ONLY", None)
        else:
            os.environ["C10_V64_RESOLVED_ACCEPTANCE_ONLY"] = self.previous

    @staticmethod
    def plan(
        *,
        candidate_move: float,
        peer_median: float,
        rank: int,
        context_aligned: bool,
    ) -> SimpleNamespace:
        return SimpleNamespace(details={
            "impulse_start_ts_ns": 1,
            "completed_4h_context": {
                "state": (
                    "BULLISH_4H_ACCEPTANCE"
                    if context_aligned
                    else "UNRESOLVED_CONTEXT"
                ),
                "aligned": context_aligned,
            },
            "market_leadership": {
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "confirmation_ts_ns": 2,
                "candidate_event_move": candidate_move,
                "peer_event_median": peer_median,
                "event_direction_rank": rank,
                "event_path_efficiency": 0.5,
                "event_standardized_displacement": 1.0,
            },
        })

    def test_distributed_acceptance_is_approved(self) -> None:
        decision = resolve_intraday_acceptance(self.plan(
            candidate_move=0.01,
            peer_median=0.005,
            rank=3,
            context_aligned=False,
        ))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.state, "DISTRIBUTED_ACCEPTANCE")

    def test_pioneer_acceptance_is_approved(self) -> None:
        decision = resolve_intraday_acceptance(self.plan(
            candidate_move=0.01,
            peer_median=-0.002,
            rank=1,
            context_aligned=True,
        ))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.state, "PIONEER_ACCEPTANCE")

    def test_unresolved_state_is_rejected(self) -> None:
        decision = resolve_intraday_acceptance(self.plan(
            candidate_move=0.01,
            peer_median=-0.002,
            rank=2,
            context_aligned=False,
        ))
        self.assertFalse(decision.approved)
        self.assertEqual(decision.state, "UNRESOLVED_ACCEPTANCE")


if __name__ == "__main__":
    unittest.main()
