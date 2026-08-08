from __future__ import annotations

from decimal import Decimal
import unittest

from c10_v63_flow_continuation import FlowShockContinuationEngine
from logic import BarObs, Direction, LogicConfig, Pool, Side


class FlowShockContinuationEngineTest(unittest.TestCase):
    @staticmethod
    def engine() -> FlowShockContinuationEngine:
        engine = FlowShockContinuationEngine(
            LogicConfig(),
            "BTCUSDT-PERP.BINANCE",
        )
        engine.bars = [
            BarObs(60_000_000_000, 99.0, 100.0, 98.5, 99.5, 100.0, 55.0),
            BarObs(120_000_000_000, 99.5, 100.2, 99.0, 100.0, 100.0, 55.0),
            BarObs(180_000_000_000, 100.0, 102.5, 101.0, 102.0, 150.0, 105.0),
        ]
        engine._index = 2
        return engine

    @staticmethod
    def target() -> Pool:
        return Pool(
            scenario_id="BTC-4H-R1-HIGH",
            side=Side.HIGH,
            level=106.0,
            source="COMPLETED_4H_AUCTION",
            candidate_ts_ns=1,
            confirmed_ts_ns=2,
            confirmed_index=0,
            expiry_index=1000,
            external=True,
        )

    def test_same_leg_plan_has_structural_prices_and_costed_r(self) -> None:
        engine = self.engine()
        plan = engine._build_plan(
            bar=engine.bars[-1],
            prev=engine.bars[-2],
            atr=1.0,
            direction=Direction.LONG,
            breakout=(60_000_000_000, 120_000_000_000, 100.0),
            stop_pivot=(90_000_000_000, 150_000_000_000, 98.8),
            target_pool=self.target(),
            relative_volume=1.5,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.expected_entry, 101.0)
        self.assertLess(plan.stop_price, plan.expected_entry)
        self.assertGreater(plan.target_price, plan.expected_entry)
        self.assertGreaterEqual(plan.net_r, engine.config.min_net_r)
        self.assertEqual(
            plan.details["target_pool_source"],
            "COMPLETED_4H_AUCTION",
        )
        self.assertEqual(engine.pending_plan_id, plan.scenario_id)

    def test_rejection_terminates_pending_state_without_trade(self) -> None:
        engine = self.engine()
        plan = engine._build_plan(
            bar=engine.bars[-1],
            prev=engine.bars[-2],
            atr=1.0,
            direction=Direction.LONG,
            breakout=(60_000_000_000, 120_000_000_000, 100.0),
            stop_pivot=(90_000_000_000, 150_000_000_000, 98.8),
            target_pool=self.target(),
            relative_volume=1.5,
        )
        assert plan is not None
        engine.mark_rejected(plan, plan.observed_ts_ns, "TEST_REJECTION")
        self.assertIsNone(engine.pending_plan_id)
        self.assertIsNone(engine.active_trade_id)
        self.assertEqual(engine.events[-1].next_state, "TERMINAL")

    def test_submission_reserves_target_and_real_fill_advances_state(self) -> None:
        engine = self.engine()
        target = self.target()
        plan = engine._build_plan(
            bar=engine.bars[-1],
            prev=engine.bars[-2],
            atr=1.0,
            direction=Direction.LONG,
            breakout=(60_000_000_000, 120_000_000_000, 100.0),
            stop_pivot=(90_000_000_000, 150_000_000_000, 98.8),
            target_pool=target,
            relative_volume=1.5,
        )
        assert plan is not None
        engine.mark_submitted(plan, Decimal("1"))
        self.assertIn(target.scenario_id, engine.used_target_ids)
        self.assertEqual(engine.active_trade_state, "PENDING_ENTRY")
        engine.mark_entry_filled(plan.observed_ts_ns)
        self.assertEqual(engine.active_trade_state, "POSITION")
        engine.mark_trade_terminal(plan.observed_ts_ns + 1, "TEST_EXIT")
        self.assertIsNone(engine.active_trade_id)

    def test_external_target_is_consumed_on_first_passage(self) -> None:
        engine = self.engine()
        target = self.target()
        target.level = 101.5
        engine.pools.append(target)
        engine._consume_external_first_passage(
            engine.bars[-1],
            engine.bars[-2],
        )
        self.assertTrue(target.consumed)
        self.assertEqual(engine.events[-1].event_type, "EXTERNAL_LIQUIDITY_CONSUMED")


if __name__ == "__main__":
    unittest.main()
